import socket

HOST = "0.0.0.0"

PORT = 5000
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))
print("UDP Echo Server Started")
while True:
    data, addr = sock.recvfrom(2048)
    sock.sendto(data, addr)