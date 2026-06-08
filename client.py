import argparse
import csv
import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime

DEFAULT_SERVER = "http://137.131.178.229:8080"
DEFAULT_SEGMENTS = 30
DEFAULT_CSV = "metrics_baseline.csv"

NET_ERRORS = (urllib.error.URLError, ConnectionError, TimeoutError,
              socket.timeout, http.client.HTTPException)

class BufferManager:
    def __init__(self, segment_duration=4.0, max_buffer=10.0):
        self.level = 0.0
        self.segment_duration = segment_duration
        self.max_buffer = max_buffer
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
    """Politica 1 (baseline): pega a maior qualidade <= vazao media * safety."""
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

class RateBasedHysteresisABR:
    """Politica 2: mesmo estimador do baseline (vazao media * safety), mas com
    slow-start e histerese. So muda de qualidade apos `confirm` segmentos
    consecutivos apontando para a mesma direcao, e se move um nivel por vez.
    Ruido de vazao que faria o baseline oscilar nao acumula confirmacao, entao
    a qualidade fica estavel. Ataca a deficiencia de oscilacao do baseline."""
    def __init__(self, representations, safety_factor=0.8, window=3, confirm=3):
        self.qualities = sorted(representations, key=lambda q: q["bitrate_kbps"])
        self.safety = safety_factor
        self.window = window
        self.confirm = confirm
        self.history = []
        self.current = 0          # slow-start: comeca na menor qualidade
        self.pending_target = None
        self.pending_count = 0

    def update(self, throughput_kbps):
        self.history.append(throughput_kbps)
        if len(self.history) > self.window:
            self.history.pop(0)

    def _target_index(self):
        if not self.history:
            return 0
        estimated = (sum(self.history) / len(self.history)) * self.safety
        target = 0
        for i, q in enumerate(self.qualities):
            if q["bitrate_kbps"] <= estimated:
                target = i
        return target

    def select(self):
        target = self._target_index()
        if target == self.current:
            self.pending_count = 0          # ja estamos onde a banda pede
            return self.qualities[self.current]
        if target == self.pending_target:
            self.pending_count += 1
        else:
            self.pending_target = target
            self.pending_count = 1
        if self.pending_count >= self.confirm:
            self.current += 1 if target > self.current else -1  # um nivel por vez
            self.pending_count = 0
        return self.qualities[self.current]

POLICIES = {"p1": RateBasedABR, "p2": RateBasedHysteresisABR}

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


def health_check(server_url, timeout=2):
    try:
        with urllib.request.urlopen(f"{server_url}/health", timeout=timeout) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except NET_ERRORS:
        return False


def fetch_manifest(server_url):
    with urllib.request.urlopen(f"{server_url}/manifest", timeout=10) as r:
        return json.loads(r.read().decode())

def resolve_servers(manifest, fallback):
    """Lista de servidores do manifest, ordenada por prioridade. Cai no fallback
    se o manifest nao trouxer a lista."""
    servers = manifest.get("servers")
    out = []
    if servers:
        for s in sorted(servers, key=lambda s: s.get("priority", 99)):
            base = s.get("url") or s.get("base_url")
            if base:
                out.append({"id": s.get("id", "A"), "url": base.rstrip("/")})
    if not out:
        out = [{"id": "A", "url": fallback}]
    return out


class FailoverController:
    """Mantem o servidor ativo e migra por prioridade quando ele falha."""
    def __init__(self, servers):
        self.servers = servers
        self.active = 0
        self.failovers = 0
        self.events = []  # (segment, from_id, to_id, failover_time_s)

    @property
    def current(self):
        return self.servers[self.active]

    def failover(self, segment_idx):
        """Procura o proximo servidor saudavel por prioridade. Retorna True se migrou."""
        t0 = time.time()
        from_id = self.current["id"]
        order = list(range(self.active + 1, len(self.servers))) + list(range(0, self.active))
        for idx in order:
            if health_check(self.servers[idx]["url"]):
                self.active = idx
                self.failovers += 1
                dt = time.time() - t0
                self.events.append((segment_idx, from_id, self.current["id"], dt))
                print(f"  >> FAILOVER seg {segment_idx}: {from_id} -> {self.current['id']} "
                      f"({dt*1000:.0f}ms p/ achar servidor saudavel)")
                return True
        return False

def parse_args():
    p = argparse.ArgumentParser(description="Cliente ABR com failover (P1 baseline / P2 histerese)")
    p.add_argument("--server", default=DEFAULT_SERVER, help="URL base do servidor de manifest")
    p.add_argument("--policy", choices=list(POLICIES), default="p1", help="politica ABR")
    p.add_argument("--confirm", type=int, default=3, help="P2: segmentos para confirmar subida")
    p.add_argument("--max-buffer", type=float, default=10.0, help="teto do buffer em s (playback real-time)")
    p.add_argument("-n", "--segments", type=int, default=DEFAULT_SEGMENTS, help="Numero de segmentos")
    p.add_argument("-o", "--output", default=DEFAULT_CSV, help="Arquivo CSV de saida")
    return p.parse_args()

def make_abr(policy, representations, confirm):
    if policy == "p2":
        return RateBasedHysteresisABR(representations, confirm=confirm)
    return RateBasedABR(representations)

def main():
    args = parse_args()

    try:
        manifest = fetch_manifest(args.server)
    except NET_ERRORS as e:
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
    servers = resolve_servers(manifest, args.server)

    buf = BufferManager(seg_dur, max_buffer=args.max_buffer)
    abr = make_abr(args.policy, representations, args.confirm)
    fo = FailoverController(servers)
    jitter_ewma = 0.0
    alpha = 0.2

    print(
        f"Manifest OK. policy={args.policy} servidores={[s['id'] for s in servers]} "
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

            # pacing: playback em tempo real com buffer limitado. Se ja ha buffer
            # suficiente, espera o playback drenar antes de buscar o proximo segmento.
            while buf.level > buf.max_buffer:
                time.sleep(buf.level - buf.max_buffer)
                buf.consume()

            elapsed = thr = jitter_net = None
            while True:
                url = f"{fo.current['url']}{q['url_path']}"
                try:
                    elapsed, thr, jitter_net = download_segment(url)
                    break
                except NET_ERRORS as e:
                    print(f"Falha em seg {i} no servidor {fo.current['id']}: {e}")
                    if not fo.failover(i):
                        print("Nenhum servidor saudavel. Interrompendo.")
                        break
            if elapsed is None:
                break

            stall = buf.consume()   # contabiliza o playback durante o download/failover
            buf.add_segment()
            jitter_ewma = alpha * jitter_net + (1 - alpha) * jitter_ewma
            abr.update(thr)
            rebuffer = 1 if stall > 0 else 0
            can_play = buf.can_play()
            w.writerow([
                i, datetime.now().isoformat(), fo.current["id"], q["quality"], q["bitrate_kbps"],
                round(thr, 2), round(elapsed, 3), round(jitter_net, 2),
                round(jitter_ewma, 2), round(buf.level, 2), can_play,
                rebuffer, round(stall, 3), fo.failovers
            ])
            print(
                f"Seg {i:3d}  |  Srv={fo.current['id']}  |  q={q['quality']:>5}  |  thr={thr:7.0f}kbps "
                f"  |  dl={elapsed:5.2f}s  |  buf={buf.level:5.2f}s  |  play={can_play} "
                f"  |  jit={jitter_net:5.1f}ms  |  stall={stall:.2f}s"
            )

    print(f"CSV gravado em {args.output} | rebuffers={buf.rebuffer_events} failovers={fo.failovers}")
    if fo.events:
        for seg, a, b, dt in fo.events:
            print(f"  failover @seg{seg}: {a}->{b} em {dt*1000:.0f}ms")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
