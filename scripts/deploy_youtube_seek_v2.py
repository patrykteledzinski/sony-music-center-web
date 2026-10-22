#!/usr/bin/env python3
"""Install/restore the versioned YouTube2 seek controller. Credentials are prompted."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
from pathlib import Path
import posixpath
import re

import paramiko

ROOT=Path(__file__).resolve().parents[1]
APP='/data/youtube-standalone/runtime/app'
PLAYER='/data/plugins/music_service/youtube2/lib/controller/play'
FILES=[
 ('integrations/youtube2-v2/play-controller.js',PLAYER+'/index.js'),
 ('integrations/youtube2-v2/seek-engine.js',PLAYER+'/seek-engine.js'),
 ('integrations/youtube2-standalone/service.py',APP+'/service.py'),
 ('sony_youtube.py',APP+'/sony_youtube.py'),
 ('sony_youtube_stream.py',APP+'/sony_youtube_stream.py'),
]
for webroot in ['/volumio/http/www','/volumio/http/www3']:
 for name in ['index.html','studio.css','studio.js','studio-seek.js']:
  FILES.append(('volumio-ui/'+name,webroot+'/studio/'+name))


def mkdirs(sftp,directory):
 try:sftp.stat(directory)
 except IOError:
  mkdirs(sftp,posixpath.dirname(directory));sftp.mkdir(directory)


def restart(client):
 for name in ['youtube-standalone','volumio']:
  stdin,stdout,stderr=client.exec_command('sudo -n systemctl restart '+name,timeout=45)
  stdout.read();error=stderr.read().decode('utf-8','replace');status=stdout.channel.recv_exit_status()
  stdin.close();stdout.close();stderr.close()
  if status:raise RuntimeError('Restart '+name+' failed: '+error)


def install(client):
 backup='/data/sony-web-backups/youtube-seek-v2-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
 entries=[]
 with client.open_sftp() as sftp:
  mkdirs(sftp,backup)
  for index,(source,target) in enumerate(FILES):
   data=(ROOT/source).read_bytes();entry={'target':target,'backup':str(index),'existed':False,'new_sha256':hashlib.sha256(data).hexdigest()}
   try:
    with sftp.open(target,'rb') as stream:previous=stream.read()
    with sftp.open(backup+'/'+str(index),'wb') as stream:stream.write(previous)
    entry.update(existed=True,old_sha256=hashlib.sha256(previous).hexdigest())
   except FileNotFoundError:pass
   entries.append(entry)
  # Persist the complete rollback map before modifying any target.
  with sftp.open(backup+'/manifest.json','w') as stream:stream.write(json.dumps(entries,indent=2))
  for (source,target),entry in zip(FILES,entries):
   temporary=target+'.seek-v2-next'
   sftp.put(str(ROOT/source),temporary);sftp.posix_rename(temporary,target)
  with sftp.open('/data/youtube-standalone/seek-v2-version.json','w') as stream:
   stream.write(json.dumps({'protocol':2,'backup':backup,'files':{e['target']:e['new_sha256'] for e in entries}},indent=2))
 return backup


def restore(client,backup):
 if not re.fullmatch(r'/data/sony-web-backups/youtube-seek-v2-\d{8}T\d{6}Z',backup):raise ValueError('Invalid backup path')
 allowed={target for _,target in FILES}
 with client.open_sftp() as sftp:
  with sftp.open(backup+'/manifest.json') as stream:entries=json.loads(stream.read())
  for entry in entries:
   target=entry['target']
   if target not in allowed:raise ValueError('Unexpected restore target')
   if entry['existed']:
    with sftp.open(backup+'/'+entry['backup'],'rb') as stream:data=stream.read()
    if hashlib.sha256(data).hexdigest()!=entry['old_sha256']:raise RuntimeError('Backup checksum mismatch')
    with sftp.open(target+'.seek-v2-restore','wb') as stream:stream.write(data)
    sftp.posix_rename(target+'.seek-v2-restore',target)
   else:
    try:sftp.remove(target)
    except FileNotFoundError:pass

  try:sftp.remove('/data/youtube-standalone/seek-v2-version.json')
  except FileNotFoundError:pass


if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--host',required=True);parser.add_argument('--user',default='volumio');parser.add_argument('--known-hosts');parser.add_argument('--restore');args=parser.parse_args()
 client=paramiko.SSHClient();client.load_system_host_keys()
 if args.known_hosts:client.load_host_keys(args.known_hosts)
 client.set_missing_host_key_policy(paramiko.RejectPolicy())
 try:
  client.connect(args.host,username=args.user,password=getpass.getpass('SSH password: '),timeout=12,look_for_keys=False,allow_agent=False)
  if args.restore:restore(client,args.restore);print('Previous files restored')
  else:print('Backup: '+install(client))
  restart(client)
 finally:client.close()
