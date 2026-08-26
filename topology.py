from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
import os

class QoS_ML_Topology(Topo):
    def build(self):
        switch = self.addSwitch('s1')
        server = self.addHost('server', ip='10.0.0.100/24')
        self.addLink(switch, server, bw=10, delay='2ms', loss=0)
        for i in range(1, 6):
            host = self.addHost(f'h{i}', ip=f'10.0.0.{i}/24')
            self.addLink(host, switch, bw=100)
def setup_queues():
    cmd = (
        "ovs-vsctl set port s1-eth1 qos=@newqos -- "
        "--id=@newqos create qos type=linux-htb other-config:max-rate=10000000 "
        "queues=0=@q0,1=@q1,2=@q2,3=@q3 -- "
        "--id=@q0 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 -- "  # گیمینگ
        "--id=@q1 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 -- "  # استریم
        "--id=@q2 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 -- "  # وب
        "--id=@q3 create queue other-config:min-rate=2000000 other-config:max-rate=10000000"     # دانلود
    )
    os.system(cmd)
    print("--------------------- Default Balanced QoS Queues Initialized ---------------------")
def run():
    topo = QoS_ML_Topology()
    net = Mininet(topo=topo, link=TCLink, controller=None)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)
    net.start()
    print("---------------------The network was successfully activated---------------------")
    setup_queues()
    server = net.get('server')
    # server.cmd('iperf -s -u -p 5000 &') 
    server.cmd('iperf -s -p 5001 &')
    server.cmd('iperf -s -p 5003 &')
    server.cmd('iperf -s -p 5002 &')
    print("---------------------The server is ready to receive traffic---------------------")
    CLI(net)
    net.stop()
if __name__ == '__main__':
    setLogLevel('info')
    run()