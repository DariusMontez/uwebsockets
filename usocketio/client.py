"""
Micropython Socket.IO client.
"""

import logging
import ure as re
import ujson as json
import usocket as socket
from ucollections import namedtuple

from .protocol import *
from .transport import SocketIO

LOGGER = logging.getLogger(__name__)

URL_RE = re.compile(r'http://([A-Za-z0-9\-\.]+)(?:\:([0-9]+))?(/.+)?')
URI = namedtuple('URI', ('hostname', 'port', 'path'))


def urlparse(uri):
    """Parse http:// URLs"""
    match = URL_RE.match(uri)
    if match:
        return URI(match.group(1), int(match.group(2) or 80), match.group(3))


def _http_get(hostname, port, path):
    sock = socket.socket()

    res = {
        "headers": {}
    }

    try:
        addr = socket.getaddrinfo(hostname, port)
        sock.connect(addr[0][4])
        
        if port == 443:
            import ussl
            sock = ussl.wrap_socket(sock, server_hostname=host)
            
        def send_header(header):
            if __debug__:
                LOGGER.debug("SEND HEADER {}"
                    .format(header, header.decode("utf-8")))
                    
            sock.write(header + '\r\n')
        
        send_header(b'GET %s HTTP/1.1' % path)
        send_header(b'Host: %s:%s' % (hostname, port))
        send_header(b'')
        
        line = sock.readline()[:-2]
        print(line, line.split(None, 2))
        res["version"], code, res["status_message"] = line.split(None, 2)
        res["status_code"] = int(code)
        
        while (True):
            header = sock.readline()[:-2]
            
            if not header:
                break
            
            k, v = [x.strip() for x in header.split(b":", 1)]
            res["headers"][k] = v
            
        def readall(sock, buffer_size=1024):
            while True:
                buf = sock.recv(buffer_size)
                yield buf
                if len(buf) < buffer_size:
                    break
                
        res["body"] = b''.join(readall(sock))
            
    finally:
      sock.close()
            
    print(res)
        
    return sock, res


def _connect_http(hostname, port, path):
    """Stage 1 do the HTTP connection to get our SID"""
    
    sock, res = _http_get(hostname, port, path)
    
    if res["status_code"] == 200:
        return decode_payload(res["body"])
        
    else:
        raise Exception(u"Bad response from server: {}".format(res))


def connect(uri):
    """Connect to a socket IO server."""
    uri = urlparse(uri)

    assert uri

    path = uri.path or '/' + 'socket.io/?EIO=3'

    # Start a HTTP connection, which will give us an SID to use to upgrade
    # the websockets connection
    packets = _connect_http(uri.hostname, uri.port, path + "&transport=polling")
    # The first packet should open the connection,
    # following packets might be initialisation messages for us
    packet_type, params = next(packets)

    assert packet_type == PACKET_OPEN
    params = json.loads(params)
    LOGGER.debug("Websocket parameters = %s", params)

    assert 'websocket' in params['upgrades']

    sid = params['sid']
    path += '&sid={}'.format(sid)

    if __debug__:
        LOGGER.debug("Connecting to websocket SID %s", sid)

    # Start a websocket and send a probe on it
    ws_uri = 'ws://{hostname}:{port}{path}&transport=websocket'.format(
        hostname=uri.hostname,
        port=uri.port,
        path=path)

    socketio = SocketIO(ws_uri, **params)

    # handle rest of the packets once we're in the main loop
    @socketio.on('connection')
    def on_connect(data):
        for packet_type, data in packets:
            socketio._handle_packet(packet_type, data)

    socketio._send_packet(PACKET_PING, 'probe')

    # Send a follow-up poll
    _connect_http(uri.hostname, uri.port, path + '&transport=polling')

    # We should receive an answer to our probe
    packet = socketio._recv()
    assert packet == (PACKET_PONG, 'probe')

    # Upgrade the connection
    socketio._send_packet(PACKET_UPGRADE)
    packet = socketio._recv()
    assert packet == (PACKET_NOOP, '')

    return socketio
