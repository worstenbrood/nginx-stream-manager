"""Conservative import of inline Nginx stream configuration; never executes it."""
import hashlib
import ipaddress
import re

class ImportError(ValueError):
    pass

def tokens(text):
    out, word, started, quote, i = [], '', False, None, 0
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 1
            if i == len(text): raise ImportError('Trailing escape in configuration')
            word += text[i]; started = True
        elif quote:
            if c == quote: quote = None
            else: word += c
        elif c in "\"'":
            quote = c; started = True
        elif c == '#':
            if started: out.append(('word', word)); word = ''; started = False
            end = text.find('\n', i); i = len(text) if end < 0 else end
            continue
        elif c.isspace() or c in '{};':
            if started: out.append(('word', word)); word = ''; started = False
            if c in '{};': out.append((c, c))
        else:
            word += c; started = True
        i += 1
    if quote: raise ImportError('Unclosed quoted string')
    if started: out.append(('word', word))
    return out

def parse(text):
    ts = tokens(text)
    pos = 0
    def block(depth=0):
        nonlocal pos
        if depth > 32: raise ImportError('Configuration nesting too deep')
        nodes, words = [], []
        while pos < len(ts):
            kind, value = ts[pos]; pos += 1
            if kind == 'word': words.append(value)
            elif kind == '}':
                if not depth or words: raise ImportError('Unexpected closing brace')
                return nodes
            elif kind in (';', '{'):
                if not words: raise ImportError('Empty directive')
                nodes.append({'name': words[0], 'args': words[1:],
                              'children': block(depth + 1) if kind == '{' else None})
                words = []
        if depth or words: raise ImportError('Incomplete directive or block')
        return nodes
    return block()

def endpoint(value):
    if re.fullmatch(r'[0-9]+', value): return '0.0.0.0', value
    if value.startswith('['):
        match = re.fullmatch(r'\[([^]]+)\]:(\d+)', value)
        if not match: raise ImportError('Invalid IPv6 endpoint: ' + value)
        host, port = match.groups()
    else:
        try: host, port = value.rsplit(':', 1)
        except ValueError: raise ImportError('Expected IP:port: ' + value)
        if host == '*': host = '0.0.0.0'
    try:
        host = str(ipaddress.ip_address(host))
        if '%' in host or not port.isdigit() or not 1 <= int(port) <= 65535: raise ValueError()
    except ValueError:
        raise ImportError('Only literal IP endpoints are supported: ' + value)
    return host, str(int(port))

def seconds(value):
    if value.isdigit(): return str(int(value))
    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    parts = re.findall(r'(\d+)([smhd])', value)
    if not parts or ''.join(a+b for a,b in parts) != value:
        raise ImportError('Unsupported time value: ' + value)
    return str(sum(int(a)*units[b] for a,b in parts))

def size(value):
    m = re.fullmatch(r'(\d+)([kKmM]?)', value)
    if not m: raise ImportError('Unsupported size value: ' + value)
    return str(int(m[1]) * {'':1,'k':1024,'m':1048576}[m[2].lower()])

def one(node):
    if node['children'] is not None or len(node['args']) != 1:
        raise ImportError('Expected one value for ' + node['name'])
    return node['args'][0]

SETTINGS = {
    'proxy_connect_timeout': ('connect_timeout', seconds),
    'proxy_timeout': ('timeout', seconds),
    'proxy_next_upstream_tries': ('next_upstream_tries', str),
    'proxy_upload_rate': ('upload_rate', size),
    'proxy_download_rate': ('download_rate', size),
    'proxy_requests': ('udp_requests', str),
    'proxy_responses': ('udp_responses', str),
}
FLAGS = {'proxy_socket_keepalive':'keepalive', 'proxy_half_close':'half_close', 'proxy_protocol':'proxy_protocol'}
ALLOWED = set(SETTINGS) | set(FLAGS) | {'listen', 'proxy_pass', 'proxy_bind', 'proxy_next_upstream',
                                     'access_log', 'error_log', 'allow', 'deny'}

def settings(nodes):
    result = {}
    seen = set()
    for n in nodes:
        name = n['name']
        if name in set(SETTINGS) | set(FLAGS) | {'proxy_bind', 'proxy_next_upstream'}:
            if name in seen:
                raise ImportError('Duplicate directive: ' + name)
            seen.add(name)
        if name in SETTINGS:
            key, converter = SETTINGS[name]; result[key] = converter(one(n))
        elif name in FLAGS:
            v = one(n)
            if v not in ('on','off'): raise ImportError('Unsupported ' + name)
            result[FLAGS[name]] = '1' if v == 'on' else '0'
        elif name == 'proxy_bind':
            if n['args'] == ['off']: result['transparent'] = '0'
            elif n['args'] == ['$remote_addr', 'transparent']: result['transparent'] = '1'
            else: raise ImportError('Only proxy_bind $remote_addr transparent or off is supported')
        elif name == 'proxy_next_upstream' and one(n) != 'on':
            raise ImportError('proxy_next_upstream off cannot be preserved by this version')
        elif name == 'access_log':
            if not n['args']: raise ImportError('Invalid access_log')
            if any(a.startswith('if=') for a in n['args']):
                raise ImportError('Conditional access_log cannot be preserved')
            if n['args'][0] == 'off' and len(n['args']) != 1:
                raise ImportError('Invalid access_log off')
            result['access_log'] = '0' if n['args'][0] == 'off' else '1'
    return result

def access(nodes):
    rules = [n for n in nodes if n['name'] in ('allow','deny')]
    if not rules: return []
    if len(rules) == 1 and rules[0]['name'] == 'allow' and one(rules[0]) == 'all': return []
    if rules[-1]['name'] != 'deny' or one(rules[-1]) != 'all':
        raise ImportError('Access rules require allow CIDRs followed by deny all')
    allowed = []
    for n in rules[:-1]:
        if n['name'] != 'allow': raise ImportError('Mixed allow/deny ordering is unsupported')
        try: allowed.append(str(ipaddress.ip_network(one(n), strict=False)))
        except ValueError: raise ImportError('Unsupported allow address')
    if not allowed: raise ImportError('deny all without allow rules cannot be represented')
    return allowed

def plan(text, existing):
    if len(text.encode()) > 262144: raise ImportError('nginx.conf exceeds 256 KiB')
    root = parse(text)
    streams = [n for n in root if n['name'] == 'stream']
    if not streams: raise ImportError('No inline stream block found in nginx.conf')
    blockers, warnings, sections, rows = [], [], [], []
    if any(n['name'] == 'include' for n in root):
        blockers.append('Top-level include found. External configuration is not followed; provide a reviewed inline stream configuration before import.')
    if len(streams) != 1 or streams[0]['children'] is None or streams[0]['args']:
        raise ImportError('Exactly one inline stream block is required')
    nodes = streams[0]['children']
    for n in nodes:
        if n['name'] not in (ALLOWED - {'listen', 'proxy_pass'}) | {'upstream','server','log_format'}:
            blockers.append('Unsupported stream directive: ' + n['name'])
        if n['name'] in ALLOWED and n['children'] is not None:
            blockers.append('Unexpected stream block: ' + n['name'])
    source_hash = hashlib.sha256(text.encode()).hexdigest()
    prefix = 'imp_' + source_hash[:8] + '_'
    existing_names = {s['.name'] for s in existing}
    if any(name.startswith(prefix) for name in existing_names):
        blockers.append('This source configuration has already been imported.')
    pool_map, pools = {}, []
    try:
        inherited = settings(nodes)
        inherited_access = access(nodes)
    except ImportError as e:
        blockers.append(str(e)); inherited, inherited_access = {}, []
    for i, n in enumerate([n for n in nodes if n['name'] == 'upstream'], 1):
        try:
            if len(n['args']) != 1 or n['children'] is None: raise ImportError('Invalid upstream block')
            name = n['args'][0]
            if len(name) > 128 or any(ord(c) < 32 for c in name): raise ImportError('Invalid or too long upstream name')
            if name in pool_map: raise ImportError('Duplicate upstream name')
            pid = prefix + 'pool_' + str(i)
            p = {'.type':'pool', '.name':pid, 'name':name, 'method':'round_robin'}
            bs = []
            balancing = False
            for child in n['children']:
                key = child['name']
                if key == 'server':
                    if not child['args'] or child['children'] is not None: raise ImportError('Invalid backend')
                    host, port = endpoint(child['args'][0])
                    b = {'.type':'backend', '.name':pid+'_b'+str(len(bs)+1), 'pool':pid,
                         'enabled':'1','address':host,'port':port}
                    for opt in child['args'][1:]:
                        if opt in ('backup','down'): b[opt]='1'
                        elif '=' in opt:
                            k,v=opt.split('=',1)
                            if k not in ('weight','max_fails','fail_timeout','max_conns'): raise ImportError('Unsupported backend option: '+k)
                            b[k] = seconds(v) if k == 'fail_timeout' else v
                        else: raise ImportError('Unsupported backend option: '+opt)
                    bs.append(b)
                elif key in ('least_conn', 'hash'):
                    if balancing or child['children'] is not None:
                        raise ImportError('Duplicate or invalid balancing directive')
                    if key == 'least_conn' and not child['args']: p['method']='least_conn'
                    elif key == 'hash' and child['args'] == ['$remote_addr','consistent']: p['method']='hash_ip'
                    else: raise ImportError('Unsupported balancing directive')
                    balancing = True
                elif key == 'zone' and child['children'] is None and len(child['args']) in (1, 2):
                    warnings.append('Upstream %s: zone is regenerated by the manager.' % name)
                else: raise ImportError('Unsupported upstream directive: '+key)
            if not bs: raise ImportError('Empty upstream: '+name)
            pool_map[name]=pid; pools += [p]+bs
        except ImportError as e: blockers.append('Upstream %d: %s' % (i,e))
    sections += pools
    for index, n in enumerate([n for n in nodes if n['name'] == 'server'], 1):
        try:
            children=n['children']
            if children is None or n['args']: raise ImportError('Invalid server block')
            for child in children:
                if child['name'] not in ALLOWED or child['children'] is not None:
                    raise ImportError('Unsupported server directive: '+child['name'])
            passes=[c for c in children if c['name']=='proxy_pass']
            if len(passes)!=1: raise ImportError('Exactly one proxy_pass is required')
            target=one(passes[0])
            local_sections=[]
            if target in pool_map: pool=pool_map[target]
            else:
                host,port=endpoint(target); pool=prefix+'direct_'+str(index)
                local_sections += [{'.type':'pool','.name':pool,'name':target,'method':'round_robin'},
                                   {'.type':'backend','.name':pool+'_b1','enabled':'1','pool':pool,'address':host,'port':port}]
            merged={'connect_timeout':'60','timeout':'600','access_log':'0'}; merged.update(inherited); merged.update(settings(children))
            acl = access(children) if any(c['name'] in ('allow','deny') for c in children) else inherited_access
            listeners={}
            for listen in [c for c in children if c['name']=='listen']:
                if not listen['args']: raise ImportError('Empty listen')
                host,port=endpoint(listen['args'][0])
                opts=listen['args'][1:]
                if any(o not in ('udp','reuseport','bind','ipv6only=on') for o in opts):
                    raise ImportError('Unsupported listen options: '+' '.join(opts))
                proto='udp' if 'udp' in opts else 'tcp'
                protos=listeners.setdefault((host,port),set())
                if proto in protos: raise ImportError('Duplicate listener in server')
                protos.add(proto)
            if not listeners: raise ImportError('Server has no listen directive')
            for li, ((host,port), protos) in enumerate(listeners.items(),1):
                sid=prefix+'svc_'+str(index)+'_'+str(li)
                service={'.type':'service','.name':sid,'name':'Imported '+host+':'+port,
                         'enabled':'0','protocol':'both' if len(protos)==2 else next(iter(protos)),
                         'listen_address':host,'listen_port':port,'pool':pool,'open_firewall':'0',
                         'return_route_ack':'0', **merged}
                if acl: service['allow_source']=acl
                local_sections.append(service)
                rows.append({'id':sid,'listen':host+':'+port,'protocol':service['protocol'],
                             'target':target,'transparent':service.get('transparent')=='1'})
            sections += local_sections
        except ImportError as e: blockers.append('Server %d: %s' % (index,e))
    if not rows: blockers.append('No importable services found')
    warnings += ['All imported services remain disabled; no proxy is started.',
                 'Transparent services need return devices and routing confirmation before activation.',
                 'Existing Nginx listeners and old TPROXY rules must be migrated manually before activation.',
                 'Global worker settings and log paths/formats are not copied; the manager uses its own settings and logs.']
    if len(sections)+len(existing)>512: blockers.append('Import would exceed 512 sections')
    return {'source_hash':source_hash,'sections':sections,'services':rows,'blockers':blockers,'warnings':warnings}

def uci_text(sections):
    def quote(value):
        if any(c in str(value) for c in ('\n','\r','\0')): raise ImportError('Multiline UCI values are unsupported')
        # shlex-compatible UCI quoting, including literal apostrophes.
        return "'"+str(value).replace("'", "'\\''")+"'"
    lines=[]
    for section in sections:
        lines.append('config %s %s' % (quote(section['.type']),quote(section['.name'])))
        for key,value in section.items():
            if key.startswith('.'): continue
            if not re.fullmatch(r'[A-Za-z0-9_]+',key): raise ImportError('Invalid UCI option name')
            for item in value if isinstance(value,list) else [value]:
                lines.append('\t%s %s %s' % ('list' if isinstance(value,list) else 'option',quote(key),quote(item)))
        lines.append('')
    return '\n'.join(lines)
