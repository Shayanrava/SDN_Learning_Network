from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

class MultiPathQoSTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        s8 = self.addSwitch('s8')
        s2 = self.addSwitch('s2')
        s5 = self.addSwitch('s5')
        s3 = self.addSwitch('s3')
        s6 = self.addSwitch('s6')
        s4 = self.addSwitch('s4')
        s7 = self.addSwitch('s7')
        server = self.addHost('server', ip='10.0.0.100/24')
        self.addLink(s1, s2, port1=2, port2=1, bw=10, delay='2ms')
        self.addLink(s1, s3, port1=3, port2=1, bw=10, delay='2ms')
        self.addLink(s1, s4, port1=4, port2=1, bw=10, delay='2ms')
        self.addLink(s2, s5, port1=2, port2=1, bw=10, delay='2ms')
        self.addLink(s3, s6, port1=2, port2=1, bw=10, delay='2ms')
        self.addLink(s4, s7, port1=2, port2=1, bw=10, delay='2ms')
        self.addLink(s5, s8, port1=2, port2=1, bw=10, delay='2ms')
        self.addLink(s6, s8, port1=2, port2=2, bw=10, delay='2ms')
        self.addLink(s7, s8, port1=2, port2=3, bw=10, delay='2ms')
        self.addLink(s8, server, port1=4, port2=1, bw=1000,delay='1ms')
        for i in range(1, 6):
            host = self.addHost(f'h{i}', ip=f'10.0.0.{i}/24')
            self.addLink(host, s1, port2=i+4, bw=100) # h1->port 5, h2->port 6, ...
def run():
    topo = MultiPathQoSTopo()
    net = Mininet(topo=topo, link=TCLink, controller=None)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)
    net.start()
    # Avoid Broadcast Loop ARP
    net.staticArp()
    server = net.get('server')
    # server.cmd('iperf -s -u -p 5000 &') # Gaming (UDP)
    server.cmd('iperf -s -p 5001 &')    # Stream (TCP)
    server.cmd('iperf -s -p 5002 &')    # Download (TCP)
    server.cmd('iperf -s -p 5003 &')    # Web (TCP)
    print("--------------------- 8-Switch 3-Path Network Ready ---------------------")
    CLI(net)
    net.stop()
if __name__ == '__main__':
    setLogLevel('info')
    run()