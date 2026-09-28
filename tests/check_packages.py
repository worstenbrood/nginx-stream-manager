"""Inspect built IPK/APK payloads and metadata without executing hooks."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


def ipk_archives(path):
    # OpenWrt IPK outer containers may be tar.gz (opkg) or ar (Debian style).
    if path.read_bytes()[:8] == b'!<arch>\n':
        names = subprocess.check_output(['ar', 't', str(path)], text=True).splitlines()
        return {name: subprocess.check_output(['ar', 'p', str(path), name]) for name in names}
    with tarfile.open(path) as archive:
        return {member.name.removeprefix('./'): archive.extractfile(member).read()
                for member in archive.getmembers() if member.isfile()}


def inspect(path):
    archives = ipk_archives(path)
    result = {}
    for part in ('control', 'data'):
        name = next(n for n in archives if n.startswith(part + '.tar'))
        with tarfile.open(fileobj=io.BytesIO(archives[name])) as archive:
            for member in archive.getmembers():
                assert member.uid == 0 and member.gid == 0, member.name
            result[part] = {m.name.removeprefix('./'): (m.mode, archive.extractfile(m).read())
                            for m in archive.getmembers() if m.isfile()}
    return result


def inspect_apk(path):
    apk = os.environ.get('APK_TOOL') or shutil.which('apk') or '/builder/staging_dir/host/bin/apk'
    metadata = json.loads(subprocess.check_output([apk, 'adbdump', '--format', 'json', str(path)], text=True))
    info = metadata['info']
    assert info['arch'] == 'noarch', info
    data = {}
    with tempfile.TemporaryDirectory(prefix='nsm-apk-check-') as directory:
        subprocess.run([apk, 'extract', '--allow-untrusted', '--no-chown', '--destination', directory, str(path)], check=True)
        for parent in metadata['paths']:
            for entry in parent.get('files', []):
                assert entry['acl']['user'] == 'root' and entry['acl']['group'] == 'root', entry
                filename = '/'.join(filter(None, (parent.get('name', ''), entry['name'])))
                candidate = Path(directory) / filename
                assert candidate.resolve().is_relative_to(Path(directory).resolve()), filename
                data[filename] = (entry['acl']['mode'], candidate.read_bytes())
    for name, content in metadata.get('scripts', {}).items():
        assert content.startswith('#!') and '\r' not in content, name
        subprocess.run(['sh', '-n'], input=content, text=True, check=True)
    control = 'Package: %s\nArchitecture: all\nDepends: %s\n' % (info['name'], ', '.join(info.get('depends', [])))
    conffiles = data.get('lib/apk/packages/' + info['name'] + '.conffiles', (0, b''))
    return {'data': data, 'control': {'control': (0, control.encode()), 'conffiles': conffiles}}


def check(directory):
    files = [p for p in Path(directory).iterdir()
             if p.suffix in ('.ipk', '.apk') and p.name.startswith(('nginx-stream-manager', 'luci-app-nginx-stream-manager'))]
    if not files:
        raise ValueError('No NSM IPK/APK artifacts found.')
    expected = {'nginx-stream-manager', 'luci-app-nginx-stream-manager'}
    seen = set()
    for path in files:
        package = inspect_apk(path) if path.suffix == '.apk' else inspect(path)
        control = package['control']['control'][1].decode()
        fields = dict(line.split(': ', 1) for line in control.splitlines() if ': ' in line and not line.startswith(' '))
        name = fields['Package']
        if name not in expected: continue
        assert name not in seen, 'Duplicate artifact: ' + name
        seen.add(name)
        assert fields['Architecture'] == 'all', fields
        data = package['data']
        for hook, (_, content) in package['control'].items():
            if hook.startswith(('preinst', 'postinst', 'prerm', 'postrm')):
                # SDK *-pkg hooks may be shell fragments sourced by the wrapper.
                assert b'\r' not in content, hook
                assert hook.endswith('-pkg') or content.startswith(b'#!'), hook
                subprocess.run(['sh', '-n'], input=content, check=True)
        if name == 'nginx-stream-manager':
            for filename in ('usr/libexec/nginx-stream-manager', 'usr/libexec/nginx-stream-manager-maintenance',
                             'usr/libexec/rpcd/luci.nginx-stream-manager', 'etc/init.d/nginx-stream-manager'):
                mode, content = data[filename]
                assert mode & 0o111 and content.startswith(b'#!') and b'\r' not in content, filename
            assert b"option enabled '0'" in data['etc/config/nginx_stream_manager'][1]
            assert 'usr/libexec/nsm_importer.py' in data
            assert 'usr/libexec/nsm_ops.py' in data
            source_root = Path(__file__).resolve().parents[1] / 'packages/nginx-stream-manager/files'
            for installed, source in (('nginx-stream-manager', 'nsm.py'), ('nsm_ops.py', 'nsm_ops.py'), ('nsm_importer.py', 'nsm_importer.py')):
                assert data['usr/libexec/' + installed][1] == (source_root/source).read_bytes(), 'Stale backend payload: ' + installed
            assert 'etc/nginx/nginx.conf' not in data
            assert b'/etc/config/nginx_stream_manager' in package['control']['conffiles'][1]
            for dep in ('nginx', 'nginx-mod-stream', 'python3', 'ip-full', 'nftables-json', 'firewall4', 'kmod-nft-socket', 'rpcd'):
                assert dep in fields['Depends'].split(', '), (dep, fields['Depends'])
        else:
            for dep in ('luci-base', 'nginx-stream-manager'):
                assert dep in fields['Depends'].split(', '), (dep, fields['Depends'])
            for filename in ('config', 'overview', 'logs', 'import', 'traffic', 'monitor', 'tools'):
                assert 'www/luci-static/resources/view/nsm/' + filename + '.js' in data
                source = Path(__file__).resolve().parents[1] / 'packages/luci-app-nginx-stream-manager/htdocs/luci-static/resources/view/nsm' / (filename + '.js')
                assert data['www/luci-static/resources/view/nsm/' + filename + '.js'][1] == source.read_bytes(), 'Stale frontend payload: ' + filename
            for directory in ('usr/share/rpcd/acl.d', 'usr/share/luci/menu.d'):
                assert directory + '/luci-app-nginx-stream-manager.json' in data
            for filename, (_, content) in data.items():
                if filename.endswith('.json'): json.loads(content)
        print('%s: architecture, metadata and payload OK (%d files)' % (path.name, len(data)))
    assert seen == expected, 'Missing packages: ' + str(expected - seen)


if __name__ == '__main__': check(sys.argv[1])
