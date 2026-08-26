from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.recoco import Timer
import time
import pandas as pd
import numpy as np
import joblib
import os
import logging

logging.getLogger("packet").setLevel(logging.CRITICAL)
log = core.getLogger()
class IntelligentQoSController(object):
    def __init__(self):
        core.openflow.addListeners(self)
        Timer(2, self._timer_stats_request, recurring=True)
        model_path = "qos_mcd_model.pkl"
        try:
            self.model = joblib.load(model_path)
            log.info(f"🧠 ML Model ({model_path}) loaded successfully!")
        except Exception as e:
            log.error(f"❌ Failed to load ML model: {e}")
            self.model = None
        self.last_bytes = 0
        self.last_packets = 0
        self.last_drops = -1
        self.last_time = time.time()
        self.recent_kbps = [] 
        self.recent_drop_diff = 0
        self.is_mitigating = False 
        self.mac_to_port = {}
        self.connections = {}
    def _handle_ConnectionUp(self, event):
        self.connections[event.dpid] = event.connection
        self.mac_to_port[event.dpid] = {}
        log.info("Switch %s connected", event.dpid)
        self._install_permanent_qos_flows(event.connection)
    def _install_permanent_qos_flows(self, connection):
        rules = [
            (17, 5000, 0),  # Gaming (UDP) -> Queue 0
            (6, 5001, 1),   # Stream (TCP) -> Queue 1
            (6, 5003, 2),   # Web (TCP) -> Queue 2
            (6, 5002, 3),   # Download (TCP) -> Queue 3
        ]
        for nw_proto, tp_dst, queue_id in rules:
            msg = of.ofp_flow_mod()
            msg.priority = 500 
            msg.idle_timeout = 0 
            msg.hard_timeout = 0 
            msg.match.dl_type = 0x0800 
            msg.match.nw_proto = nw_proto
            msg.match.tp_dst = tp_dst
            msg.actions.append(of.ofp_action_enqueue(port=1, queue_id=queue_id))
            connection.send(msg)
        log.info("📌 Permanent QoS flows installed on Switch.")
    def _flood(self, event):
        msg = of.ofp_packet_out()
        msg.data = event.ofp
        msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
        event.connection.send(msg) 
    def _install_output_flow(self, event, out_port):
        msg = of.ofp_flow_mod()
        msg.priority = 10 
        msg.match = of.ofp_match.from_packet(event.parsed)
        msg.idle_timeout = 10
        msg.hard_timeout = 30
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.data = event.ofp
        event.connection.send(msg) 
    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            return
        dpid = event.dpid
        in_port = event.port
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][packet.src] = in_port
        if packet.dst.is_multicast or packet.dst not in self.mac_to_port[dpid]:
            self._flood(event)
            return
        out_port = self.mac_to_port[dpid][packet.dst]
        self._install_output_flow(event, out_port)    
    def _timer_stats_request(self):
        for connection in core.openflow.connections.values():
            connection.send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))
            connection.send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    def _handle_PortStatsReceived(self, event):
        total_drops = sum((stat.rx_dropped + stat.tx_dropped + stat.rx_errors + stat.tx_errors) for stat in event.stats)
        if self.last_drops == -1:
            self.last_drops = total_drops
            return
        drop_diff = total_drops - self.last_drops
        if drop_diff < 0:
            drop_diff = total_drops
        self.recent_drop_diff = drop_diff
        self.last_drops = total_drops
    def _handle_FlowStatsReceived(self, event):
        total_bytes = sum(stat.byte_count for stat in event.stats)
        total_packets = sum(stat.packet_count for stat in event.stats)
        udp_pkts = sum(stat.packet_count for stat in event.stats if getattr(stat.match, 'nw_proto', None) == 17)
        tcp_pkts = sum(stat.packet_count for stat in event.stats if getattr(stat.match, 'nw_proto', None) == 6)
        udp_tcp_ratio = (udp_pkts / tcp_pkts) if tcp_pkts > 0 else (float(udp_pkts) if udp_pkts > 0 else 0.0)
        current_time = time.time()
        time_diff = current_time - self.last_time
        if time_diff > 0:
            byte_diff = total_bytes - self.last_bytes
            packet_diff = total_packets - self.last_packets
            if byte_diff < 0:
                byte_diff = total_bytes
            if packet_diff < 0:
                packet_diff = total_packets
            kbps = (byte_diff * 8) / (time_diff * 1024) if (byte_diff > 0 and self.last_bytes > 0) else 0.0
            avg_pkt_size = (byte_diff / packet_diff) if (packet_diff > 0 and byte_diff > 0) else 0.0
            self.recent_kbps.append(kbps)
            if len(self.recent_kbps) > 3:
                self.recent_kbps.pop(0)
            kbps_diff = kbps - (self.recent_kbps[-2] if len(self.recent_kbps) >= 2 else kbps)
            kbps_rolling_avg = float(np.mean(self.recent_kbps))
            total_packets_in_window = packet_diff + self.recent_drop_diff
            if total_packets_in_window > 0:
                drop_rate = float(self.recent_drop_diff) / float(total_packets_in_window)
            else:
                drop_rate = 0.0
            input_data = pd.DataFrame([[
                kbps, 
                kbps_diff, 
                kbps_rolling_avg, 
                avg_pkt_size, 
                udp_tcp_ratio, 
                drop_rate
            ]], columns=[
                'kbps', 
                'kbps_diff', 
                'kbps_rolling_avg', 
                'avg_pkt_size', 
                'udp_tcp_ratio', 
                'drop_rate'
            ])
            prediction = self.model.predict(input_data)[0] if self.model else 0
            log.info(
                f"📊 [Features] Kbps: {kbps:.1f} | Diff: {kbps_diff:.1f} | "
                f"RollAvg: {kbps_rolling_avg:.1f} | PktSize: {avg_pkt_size:.1f}B | "
                f"UDP/TCP: {udp_tcp_ratio:.2f} | Drop: {drop_rate:.4f} "
                f"=> ML: {'⚠️ CONGESTED (1)' if prediction == 1 else '✅ NORMAL (0)'}"
            )
            if prediction == 1 and not self.is_mitigating:
                log.warn("🚨 Congestion predicted! Boosting High-Priority Queues...")
                self.apply_qos_policy()
                self.is_mitigating = True
            elif prediction == 0 and self.is_mitigating:
                log.info("🟢 Traffic back to normal. Restoring Default Queue rates...")
                self.remove_qos_policy()
                self.is_mitigating = False
            self.last_bytes = total_bytes
            self.last_packets = total_packets
            self.last_time = current_time
    def apply_qos_policy(self):
        cmd = (
            "ovs-vsctl set port s1-eth1 qos=@newqos -- "
            "--id=@newqos create qos type=linux-htb other-config:max-rate=10000000 "
            "queues=0=@q0,1=@q1,2=@q2,3=@q3 -- "
            "--id=@q0 create queue other-config:min-rate=7000000 other-config:max-rate=10000000 -- "
            "--id=@q1 create queue other-config:min-rate=2000000 other-config:max-rate=5000000 -- "
            "--id=@q2 create queue other-config:min-rate=800000 other-config:max-rate=2000000 -- "
            "--id=@q3 create queue other-config:min-rate=100000 other-config:max-rate=500000"
        )
        os.system(cmd)
        log.info("QoS Policy Applied: Gaming priority boosted (Min 7Mbps), Bulk download throttled.")
    def remove_qos_policy(self):
        cmd = (
            "ovs-vsctl set port s1-eth1 qos=@newqos -- "
            "--id=@newqos create qos type=linux-htb other-config:max-rate=10000000 "
            "queues=0=@q0,1=@q1,2=@q2,3=@q3 -- "
            "--id=@q0 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 -- "
            "--id=@q1 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 -- "
            "--id=@q2 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 -- "
            "--id=@q3 create queue other-config:min-rate=2000000 other-config:max-rate=10000000"
        )
        os.system(cmd)
        log.info("QoS Restored: Default queue rates applied.")
def launch():
    core.registerNew(IntelligentQoSController)