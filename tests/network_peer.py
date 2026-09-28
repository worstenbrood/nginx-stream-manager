"""Small real request/reply peer for the isolated network-namespace tests."""
import socket
import sys
import threading

def serve(sock, udp):
    while True:
        if udp:
            _, peer = sock.recvfrom(1024)
            sock.sendto(peer[0].encode(), peer)
        else:
            client, peer = sock.accept()
            with client:
                client.recv(1024)
                client.sendall(peer[0].encode())

if sys.argv[1] == 'server':
    for family, ip in ((socket.AF_INET, '10.232.0.2'), (socket.AF_INET6, 'fd00:232::2')):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            sock = socket.socket(family, kind)
            sock.bind((ip, 19057))
            if kind == socket.SOCK_STREAM: sock.listen()
            threading.Thread(target=serve, args=(sock, kind == socket.SOCK_DGRAM), daemon=True).start()
    print('ready', flush=True)
    threading.Event().wait()
else:
    ip, protocol = sys.argv[2:]
    with socket.socket(socket.AF_INET6 if ':' in ip else socket.AF_INET,
                       socket.SOCK_DGRAM if protocol == 'udp' else socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect((ip, 19056)); client.send(b'probe')
        print(client.recv(1024).decode())
