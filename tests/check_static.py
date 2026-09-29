"""Syntax checks only; does not claim LuCI, procd or package-build coverage."""
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
backend = root / 'packages/nginx-stream-manager/files'
shells = [root / 'install.sh', root / 'uninstall.sh'] + [backend / p for p in ('nginx-stream-manager.init', 'rpc', 'hotplug', 'maintenance')]
shells += list((root / 'tests').glob('*.sh'))
for path in shells:
    if b'\r' in path.read_bytes():
        raise RuntimeError('Shell scripts require LF line endings: ' + str(path))
    subprocess.run(['sh', '-n', str(path)], check=True)
for path in root.glob('packages/*/Makefile'):
    for hook in re.findall(r'define Package/[^\n]+/(?:preinst|postinst|prerm|postrm)\n(.*?)\nendef', path.read_text(), re.S):
        subprocess.run(['sh', '-n'], input=hook.replace('$$', '$'), text=True, check=True)
scripts = list(root.glob('packages/luci-*/htdocs/**/*.js'))
for path in scripts:
    subprocess.run(['node', '--check', str(path)], check=True)
subprocess.run(['node', str(root / 'tests/check_config_ui.js')], check=True)
subprocess.run(['node', str(root / 'tests/check_traffic_graph.js')], check=True)
subprocess.run(['node', str(root / 'tests/check_tools_ui.js')], check=True)
jsons = list(root.glob('packages/luci-*/root/**/*.json'))
for path in jsons:
    json.loads(path.read_text())
print('%d shell scripts + package hooks, %d JavaScript files, %d JSON files: syntax OK' % (len(shells), len(scripts), len(jsons)))
