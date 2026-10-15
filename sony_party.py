"""Sony PARTY controls use their own SOAP namespace and endpoint."""
import html
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

NAMESPACE = 'urn:schemas-sony-com:service:Party:1'
SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'


class PartyError(RuntimeError):
    pass


class PartyConflict(PartyError):
    pass


class PartyClient:
    def __init__(self, base_url, timeout=4):
        self.url = base_url.rstrip('/') + '/Party_Control'
        self.timeout = timeout
        self.lock = threading.RLock()

    def call(self, action, **arguments):
        arguments_xml = ''.join(f'<{name}>{html.escape(str(value))}</{name}>'
                                for name, value in arguments.items())
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{SOAP}" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{NAMESPACE}">{arguments_xml}'
            f'</u:{action}></s:Body></s:Envelope>'
        )
        req = urllib.request.Request(
            self.url, data=envelope.encode('utf-8'),
            headers={'Content-Type': 'text/xml; charset="utf-8"',
                     'SOAPAction': f'"{NAMESPACE}#{action}"', 'Connection': 'close'},
            method='POST',
        )
        http_error = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = response.read(1024 * 1024)
        except urllib.error.HTTPError as exc:
            http_error = exc.code
            payload = exc.read(1024 * 1024)
        except (OSError, urllib.error.URLError) as exc:
            raise PartyError(f'Brak połączenia z Sony PARTY: {exc}') from exc
        try:
            response = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise PartyError(f'Sony PARTY {action}: nieprawidłowa odpowiedź XML') from exc
        if any(node.tag.split('}')[-1] == 'Fault' for node in response.iter()):
            values = {node.tag.split('}')[-1]: node.text or '' for node in response.iter()}
            error = PartyError(f"Sony PARTY {action}: UPnP {values.get('errorCode', '?')} — "
                               f"{values.get('errorDescription') or values.get('faultstring', 'Błąd SOAP')}")
            error.upnp_code = values.get('errorCode')
            raise error
        if http_error:
            raise PartyError(f'Sony PARTY {action}: HTTP {http_error}')
        result = response.find(f'.//{{{NAMESPACE}}}{action}Response')
        if result is None:
            raise PartyError(f'Sony PARTY {action}: brak potwierdzenia akcji')
        return {node.tag.split('}')[-1]: node.text or '' for node in result}

    def status(self):
        with self.lock:
            values = self.call('X_GetState')
            try:
                state = values['PartyState']
                if not state or not isinstance(state, str):
                    raise ValueError()
                return dict(state=state, mode=values.get('PartyMode', ''),
                            session_id=int(values.get('SessionID', '0')),
                            listeners=int(values.get('NumberOfListeners', '0')),
                            listener_list=values.get('ListenerList', ''))
            except (KeyError, ValueError, TypeError) as exc:
                raise PartyError('Sony PARTY: nieprawidłowy stan sesji') from exc

    def wait_state(self, expected):
        for attempt in range(31):
            status = self.status()
            if status['state'] == expected:
                return status
            if attempt < 30:
                time.sleep(0.2)
        raise PartyError(f"Sony nie potwierdziło zmiany PARTY: {status['state']}")

    def change(self, action, expected, **arguments):
        for attempt in range(7):
            try:
                self.call(action, **arguments)
                return self.wait_state(expected)
            except PartyError as exc:
                if getattr(exc, 'upnp_code', None) != '501' or attempt == 6:
                    raise
                # Sony may still be settling after a prior start/stop.
                current = self.status()
                if current['state'] == expected:
                    return current
                required = 'IDLE' if action == 'X_Start' else 'SINGING'
                if current['state'] != required:
                    return self.wait_state(expected)
                if action == 'X_Abort':
                    if current['session_id'] <= 0:
                        raise PartyConflict('Sony nie podało poprawnego identyfikatora PARTY')
                    arguments['SingerSessionID'] = current['session_id']
                time.sleep(1)

    def start(self):
        with self.lock:
            status = self.status()
            if status['state'] == 'SINGING':
                return status
            if status['state'] != 'IDLE':
                raise PartyConflict('Sony uczestniczy w innej sesji PARTY; nie można uruchomić własnej')
            return self.change('X_Start', 'SINGING', PartyMode='PARTY', ListenerList='')

    def stop(self):
        with self.lock:
            status = self.status()
            if status['state'] == 'IDLE':
                return status
            if status['state'] != 'SINGING':
                raise PartyConflict('Sony jest uczestnikiem innej sesji; Party Off zatrzymuje tylko sesję prowadzoną przez Sony')
            if status['session_id'] <= 0:
                raise PartyConflict('Sony nie podało poprawnego identyfikatora sesji PARTY')
            return self.change('X_Abort', 'IDLE', SingerSessionID=status['session_id'])
