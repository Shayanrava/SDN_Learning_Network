from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.recoco import Timer
import time
import pandas as pd
import numpy as np
import joblib

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
    def _handle_ConnectionUp(self, event):
        log.info("Switch S%s connected to controller", event.dpid)
    def _flood(self, event):
        dpid = event.dpid
        in_port = event.port
        msg = of.ofp_packet_out()
        msg.data = event.ofp
        if dpid == 1:
            out_ports = [2, 5, 6, 7, 8, 9]
            for p in out_ports:
                if p != in_port:
                    msg.actions.append(of.ofp_action_output(port=p))
        elif dpid == 8:
            out_ports = [1, 4]
            for p in out_ports:
                if p != in_port:
                    msg.actions.append(of.ofp_action_output(port=p))
        else:
            out_p = 2 if in_port == 1 else 1
            msg.actions.append(of.ofp_action_output(port=out_p))
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
        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.match = of.ofp_match.from_packet(event.parsed)
        msg.idle_timeout = 10
        msg.hard_timeout = 30
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.data = event.ofp
        event.connection.send(msg)
    def _timer_stats_request(self):
        for connection in core.openflow.connections.values():
            connection.send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))
            connection.send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    def _handle_PortStatsReceived(self, event):
        if event.dpid != 1:
            return
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
        if event.dpid != 1:
            return
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
            try:
                prediction = self.model.predict(input_data)[0] if self.model else 0
            except Exception as e:
                prediction = 0
            log.info(
                f"📊 [Features] Kbps: {kbps:.1f} | Diff: {kbps_diff:.1f} | "
                f"RollAvg: {kbps_rolling_avg:.1f} | PktSize: {avg_pkt_size:.1f}B | "
                f"UDP/TCP: {udp_tcp_ratio:.2f} | Drop: {drop_rate:.4f} "
                f"=> ML: {'⚠️ CONGESTED (1)' if prediction == 1 else '✅ NORMAL (0)'}"
            )
            conn_s1 = core.openflow.connections.get(1)
            conn_s8 = core.openflow.connections.get(8)
            if prediction == 1 and not self.is_mitigating:
                log.warn("🚨 Congestion predicted! Rerouting Gaming -> Path 2 (S3-S6) & Stream -> Path 3 (S4-S7)...")
                if conn_s1:
                    self.apply_reroute_policy(conn_s1, conn_s8)
                self.is_mitigating = True
            elif prediction == 0 and self.is_mitigating:
                log.info("🟢 Traffic back to normal. Restoring all flows to Path 1 (S2-S5)...")
                if conn_s1:
                    self.remove_reroute_policy(conn_s1, conn_s8)
                self.is_mitigating = False
            self.last_bytes = total_bytes
            self.last_packets = total_packets
            self.last_time = current_time
    def apply_reroute_policy(self, connection_s1, connection_s8=None):
        msg_gaming = of.ofp_flow_mod()
        msg_gaming.priority = 1000
        msg_gaming.match.dl_type = 0x0800
        msg_gaming.match.nw_proto = 17
        msg_gaming.match.tp_dst = 5000
        msg_gaming.actions.append(of.ofp_action_output(port=3))
        connection_s1.send(msg_gaming)
        msg_stream = of.ofp_flow_mod()
        msg_stream.priority = 1000
        msg_stream.match.dl_type = 0x0800
        msg_stream.match.nw_proto = 6
        msg_stream.match.tp_dst = 5001
        msg_stream.actions.append(of.ofp_action_output(port=4))
        connection_s1.send(msg_stream)
        if connection_s8:
            msg_back_gaming = of.ofp_flow_mod()
            msg_back_gaming.priority = 1000
            msg_back_gaming.match.dl_type = 0x0800
            msg_back_gaming.match.nw_proto = 17
            msg_back_gaming.match.tp_src = 5000
            msg_back_gaming.actions.append(of.ofp_action_output(port=2))
            connection_s8.send(msg_back_gaming)
            msg_back_live_stream = of.ofp_flow_mod()
            msg_back_live_stream.priority = 1000
            msg_back_live_stream.match.dl_type = 0x0800
            msg_back_live_stream.match.nw_proto = 6
            msg_back_live_stream.match.tp_src = 5001
            msg_back_live_stream.actions.append(of.ofp_action_output(port=3))
            connection_s8.send(msg_back_live_stream)
        log.info("Multi-Path Active: Forward & Return paths installed.")
    def remove_reroute_policy(self, connection1, connection8=None):
        for proto, port in [(17, 5000), (6, 5001)]:
            msg = of.ofp_flow_mod()
            msg.command = of.OFPFC_DELETE
            msg.match.dl_type = 0x0800
            msg.match.nw_proto = proto
            msg.match.tp_dst = port
            connection1.send(msg)
        if connection8:
            for proto, port in [(17, 5000), (6, 5001)]:
                msg = of.ofp_flow_mod()
                msg.command = of.OFPFC_DELETE
                msg.match.dl_type = 0x0800
                msg.match.nw_proto = proto
                msg.match.tp_src = port
                connection8.send(msg)
        log.info("Policy Cleared: All traffic restored to Default Path (S2-S5).")
def launch():
    core.registerNew(IntelligentQoSController)
