from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.recoco import Timer
import time
import csv

log = core.getLogger()
class S1CriteriaCollector(object):
    def __init__(self):
        core.openflow.addListeners(self)
        Timer(2, self._timer_stats_request, recurring=True)
        self.csv_file = open("dataset_mcd.csv", "w", newline="")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow([
            "timestamp", "kbps", "kbps_diff", "avg_pkt_size", 
            "udp_tcp_ratio", "drop_rate", "is_congested"
        ])
        self.last_bytes = -1
        self.last_packets = -1
        self.last_drops = -1
        self.last_kbps = 0
        self.last_time = time.time()
        self.current_drop_rate = 0.0
    def _timer_stats_request(self):
        for dpid, connection in core.openflow.connections.items():
            if dpid == 1:
                # درخواست همزمان آمار جریان‌ها و آمار پورت‌ها
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
        # ذخیره تعداد بسته‌های افتاده در بازه ۲ ثانیه‌ای اخیر
        self.recent_drop_diff = drop_diff
        self.last_drops = total_drops
    def _handle_FlowStatsReceived(self, event):
        if event.dpid != 1:
            return
        total_bytes = 0
        total_packets = 0
        udp_bytes = 0
        tcp_bytes = 0
        for stat in event.stats:
            total_bytes += stat.byte_count
            total_packets += stat.packet_count
            nw_proto = getattr(stat.match, 'nw_proto', None)
            if nw_proto == 17:      # UDP
                udp_bytes += stat.byte_count
            elif nw_proto == 6:     # TCP
                tcp_bytes += stat.byte_count
        current_time = time.time()
        if self.last_bytes == -1:
            self.last_bytes = total_bytes
            self.last_packets = total_packets
            self.last_time = current_time
            return
        time_diff = current_time - self.last_time
        if time_diff > 0:
            byte_diff = total_bytes - self.last_bytes
            packet_diff = total_packets - self.last_packets
            if byte_diff < 0: 
                byte_diff = total_bytes
            if packet_diff < 0:
                packet_diff = total_packets
            kbps = (byte_diff * 8) / (time_diff * 1024)
            kbps_diff = kbps - self.last_kbps
            avg_pkt_size = byte_diff / packet_diff if packet_diff > 0 else 0
            udp_tcp_ratio = udp_bytes / (tcp_bytes + 1)
            # ۳. محاسبه واقعی drop_rate (بسته‌های افتاده نسبت به کل بسته‌ها)
            drop_diff = getattr(self, 'recent_drop_diff', 0)
            total_packets_in_window = packet_diff + drop_diff
            if total_packets_in_window > 0:
                drop_rate = (drop_diff / float(total_packets_in_window))
            else:
                drop_rate = 0.0
            is_congested = 1 if (
                kbps > 7500 or 
                drop_rate > 0.02 or   #  بیش از ۲ درصد افت بسته -> ازدحام
                (avg_pkt_size < 150 and kbps > 3000) or 
                (udp_tcp_ratio > 4.0 and kbps > 4000)
            ) else 0
            self.writer.writerow([
                round(current_time, 2), round(kbps, 2), round(kbps_diff, 2),
                round(avg_pkt_size, 2), round(udp_tcp_ratio, 2), 
                round(drop_rate, 4), is_congested
            ])
            self.csv_file.flush()
            log.info(
                f"[S1 Stats] Traffic: {round(kbps, 2)} Kbps | "
                f"Drop Rate: {round(drop_rate * 100, 2)}% | Congested: {is_congested}"
            )
            self.last_bytes = total_bytes
            self.last_packets = total_packets
            self.last_kbps = kbps
            self.last_time = current_time
def launch():
    core.registerNew(S1CriteriaCollector)