import socket
import time

HOST = "10.0.0.100"

PORT = 5000
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2)
for i in range(20):
    start = time.time()
    sock.sendto(b"game", (HOST, PORT))
    data, _ = sock.recvfrom(2048)
    end = time.time()
    print(f"RTT = {(end-start)*1000:.2f} ms")
    time.sleep(2)