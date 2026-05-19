import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime

DEFAULT_SERVER = "http://137.131.178.229:8080"
DEFAULT_SEGMENTS = 30
DEFAULT_CSV = "metrics_baseline.csv"

class BufferManager:
    def __init__(self, segment_duration=4.0):
        self.level = 0.0
        self.segment_duration = segment_duration
        self.last_update = None
        self.rebuffer_events = 0

    def consume(self):
        now = time.time()
        if self.last_update is None:
            self.last_update = now
            return 0.0
        elapsed = now - self.last_update
        self.last_update = now
        if self.level >= elapsed:
            self.level -= elapsed
            return 0.0
        stall = elapsed - self.level
        self.level = 0.0
        self.rebuffer_events += 1
        return stall

    def add_segment(self):
        self.level += self.segment_duration

    def can_play(self, threshold=2.0):
        return 1 if self.level >= threshold else 0

class RateBasedABR:
    def __init__(self, representations, safety_factor=0.8, window=3):
        self.qualities = sorted(representations, key=lambda q: q["bitrate_kbps"])
        self.safety = safety_factor
        self.window = window
        self.history = []

    def update(self, throughput_kbps):
        self.history.append(throughput_kbps)
        if len(self.history) > self.window:
            self.history.pop(0)

    def select(self):
        if not self.history:
            return self.qualities[0]
        estimated = (sum(self.history) / len(self.history)) * self.safety
        chosen = self.qualities[0]
        for q in self.qualities:
            if q["bitrate_kbps"] <= estimated:
                chosen = q
        return chosen

def compute_jitter(chunk_times):
    if len(chunk_times) < 2:
        return 0.0
    diffs = [abs(chunk_times[i] - chunk_times[i - 1]) for i in range(1, len(chunk_times))]
    return (sum(diffs) / len(diffs)) * 1000


def download_segment(url, timeout=15):
    chunk_times = []
    start = time.time()
    prev = start
    total_bytes = 0
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            now = time.time()
            chunk_times.append(now - prev)
            prev = now
            total_bytes += len(chunk)
    elapsed = time.time() - start
    throughput = (total_bytes * 8 / 1000) / elapsed if elapsed > 0 else 0
    return elapsed, throughput, compute_jitter(chunk_times)


def fetch_manifest(server_url):
    with urllib.request.urlopen(f"{server_url}/manifest", timeout=10) as r:
        return json.loads(r.read().decode())

def resolve_server(manifest, fallback):
    servers = manifest.get("servers")
    if servers:
        primary = sorted(servers, key=lambda s: s.get("priority", 99))[0]
        base = primary.get("url") or primary.get("base_url")
        sid = primary.get("id", "A")
        if base:
            return base.rstrip("/"), sid
    return fallback, "A"

def parse_args():
    p = argparse.ArgumentParser(description="Cliente baseline ABR (Politica 1 - Rate-Based)")
    p.add_argument("--server", default=DEFAULT_SERVER, help="URL base do servidor de manifest")
    p.add_argument("-n", "--segments", type=int, default=DEFAULT_SEGMENTS, help="Numero de segmentos a baixar")
    p.add_argument("-o", "--output", default=DEFAULT_CSV, help="Arquivo CSV de saida")
    return p.parse_args()

def main():
    args = parse_args()

    try:
        manifest = fetch_manifest(args.server)
    except urllib.error.URLError as e:
        print(f"Erro ao obter manifest de {args.server}: {e}")
        return 1

    representations = manifest.get("representations") or manifest.get("qualities")
    if not representations:
        print("Manifest sem 'representations'/'qualities'.")
        return 1
    for r in representations:
        r.setdefault("quality", r.get("name"))
        r.setdefault("url_path", f"/segment/{r['quality']}")
    seg_dur = float(manifest.get("segment_duration_s", 4.0))
    server_url, server_id = resolve_server(manifest, args.server)

    buf = BufferManager(seg_dur)
    abr = RateBasedABR(representations)
    jitter_ewma = 0.0
    alpha = 0.2

    print(
        f"Manifest OK. Servidor={server_url} ({server_id}) "
        f"qualidades={[q['quality'] for q in representations]} seg_dur={seg_dur}s"
    )

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "segment", "timestamp", "server_id", "quality", "bitrate_kbps",
            "throughput_kbps", "download_time_s", "jitter_network_ms",
            "jitter_ewma_ms", "buffer_level_s", "buffer_can_play",
            "rebuffer_event", "stall_duration_s", "failover_total"
        ])

        for i in range(args.segments):
            q = abr.select()
            url = f"{server_url}{q['url_path']}"
            stall = buf.consume()
            try:
                elapsed, thr, jitter_net = download_segment(url)
            except urllib.error.URLError as e:
                print(f"Falha em seg {i}: {e}. Interrompendo.")
                break
            buf.add_segment()
            jitter_ewma = alpha * jitter_net + (1 - alpha) * jitter_ewma
            abr.update(thr)
            rebuffer = 1 if stall > 0 else 0
            can_play = buf.can_play()
            w.writerow([
                i, datetime.now().isoformat(), server_id, q["quality"], q["bitrate_kbps"],
                round(thr, 2), round(elapsed, 3), round(jitter_net, 2),
                round(jitter_ewma, 2), round(buf.level, 2), can_play,
                rebuffer, round(stall, 3), 0
            ])
            print(
                f"Seg {i:3d} q={q['quality']:>5} thr={thr:7.0f}kbps "
                f"dl={elapsed:5.2f}s buf={buf.level:5.2f}s play={can_play} "
                f"jit={jitter_net:5.1f}ms stall={stall:.2f}s"
            )

    print(f"CSV gravado em {args.output} | rebuffer_events={buf.rebuffer_events}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
