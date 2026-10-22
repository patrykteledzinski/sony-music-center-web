#!/usr/bin/env python3
"""Install Studio on Volumio 2. Credentials are prompted, never stored.

First verify the Pi's host key with ssh. Requires paramiko on the deploying machine.
"""
import argparse
from datetime import datetime, timezone
import getpass
from pathlib import Path
import posixpath

import paramiko

ROOTS = ('/volumio/http/www', '/volumio/http/www3')
SOURCE = Path(__file__).resolve().parents[1] / 'volumio-ui'
GUARD = '''<script id="studio-switch">
(function(){try {
var query=new URLSearchParams(location.search);
if(query.get('classic')==='1')localStorage.setItem('studioMode','classic');
if(query.get('studio')==='1')localStorage.setItem('studioMode','studio');
if(localStorage.getItem('studioMode')!=='classic')location.replace('/studio/');
}catch(error){}})();
</script>'''
SWITCH = '''<a id="studio-return" href="/?studio=1" style="position:fixed;right:16px;bottom:85px;z-index:2147483647;background:#c6edac;color:#1a2819;padding:10px 15px;border-radius:20px;font:13px Arial;text-decoration:none;box-shadow:0 4px 20px #0005">Studio ↗</a>'''


def mkdirs(sftp, directory):
    try:
        sftp.stat(directory)
    except IOError:
        mkdirs(sftp, posixpath.dirname(directory))
        sftp.mkdir(directory)


def deploy(client):
    backup = '/data/studio-ui-backups/' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    with client.open_sftp() as sftp:
        mkdirs(sftp, backup)
        for root in ROOTS:
            try:
                sftp.stat(root)
            except IOError:
                continue
            mkdirs(sftp, root + '/studio')
            for path in SOURCE.iterdir():
                if path.is_file():
                    sftp.put(str(path), root + '/studio/' + path.name)
            # Retain the pristine original across repeated deployments.
            original_path = root + '/studio-original.html'
            try:
                with sftp.open(original_path) as stream:
                    original = stream.read().decode('utf-8')
            except IOError:
                with sftp.open(root + '/index.html') as stream:
                    original = stream.read().decode('utf-8')
                with sftp.open(original_path, 'w') as stream:
                    stream.write(original)
            with sftp.open(root + '/index.html') as stream:
                current = stream.read()
            with sftp.open(backup + '/' + posixpath.basename(root) + '-index.html', 'w') as stream:
                stream.write(current)
            patched = original.replace('<head>', '<head>' + GUARD, 1)
            patched = patched.replace('</body>', SWITCH + '</body>', 1)
            temporary = root + '/index.studio-next.html'
            with sftp.open(temporary, 'w') as stream:
                stream.write(patched)
            sftp.posix_rename(temporary, root + '/index.html')
    return backup


def restore(client):
    with client.open_sftp() as sftp:
        for root in ROOTS:
            try:
                with sftp.open(root + '/studio-original.html') as stream:
                    original = stream.read()
            except IOError:
                continue
            temporary = root + '/index.studio-restore.html'
            with sftp.open(temporary, 'w') as stream:
                stream.write(original)
            sftp.posix_rename(temporary, root + '/index.html')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--user', default='volumio')
    parser.add_argument('--known-hosts')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if args.known_hosts:
        client.load_host_keys(args.known_hosts)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(args.host, username=args.user, password=getpass.getpass('SSH password: '),
                       look_for_keys=False, allow_agent=False, timeout=8)
        if args.restore:
            restore(client)
            print('Original home page restored.')
        else:
            print('Studio installed. Backup: ' + deploy(client))
    finally:
        client.close()
