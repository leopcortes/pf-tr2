import argparse
import csv
import http.client
import json
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

DEFAULT_SERVER = "http://137.131.178.229:8080"
DEFAULT_SEGMENTS = 30
DEFAULT_CSV = "metrics_baseline.csv"

NET_ERRORS = (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout, http.client.HTTPException)

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
        self.last_estimate = 0.0
        self.last_reason = "sem historico"

    def update(self, throughput_kbps, jitter_ewma_ms=0.0):
        self.history.append(throughput_kbps)
        if len(self.history) > self.window:
            self.history.pop(0)

    def select(self):
        if not self.history:
            self.last_estimate = 0.0
            self.last_reason = "sem historico -> menor q"
            return self.qualities[0]
        estimated = (sum(self.history) / len(self.history)) * self.safety
        chosen = self.qualities[0]
        for q in self.qualities:
            if q["bitrate_kbps"] <= estimated:
                chosen = q
        self.last_estimate = estimated
        self.last_reason = f"media x{self.safety:g} = {estimated:.0f} -> {chosen['quality']}"
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
        self.current = 0 # slow-start: comeca na menor qualidade
        self.pending_target = None
        self.pending_count = 0
        self.last_estimate = 0.0
        self.last_reason = "slow-start"

    def update(self, throughput_kbps, jitter_ewma_ms=0.0):
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
        self.last_estimate = (sum(self.history) / len(self.history)) * self.safety if self.history else 0.0
        if target == self.current:
            self.pending_count = 0 # ja estamos onde a banda pede
            self.last_reason = f"alvo={self.qualities[target]['quality']} (estavel)"
            return self.qualities[self.current]
        if target == self.pending_target:
            self.pending_count += 1
        else:
            self.pending_target = target
            self.pending_count = 1
        if self.pending_count >= self.confirm:
            self.current += 1 if target > self.current else -1 # um nivel por vez
            self.pending_count = 0
        self.last_reason = (f"alvo={self.qualities[target]['quality']} "
                            f"conf {self.pending_count}/{self.confirm} -> {self.qualities[self.current]['quality']}")
        return self.qualities[self.current]

class EwmaStdJitterABR:
    """Politica 3: estimativa conservadora com componente estatistico e
    sensibilidade a jitter. Tres ideias somadas:

      1. EWMA da vazao  -> a vazao recente pesa mais que o passado distante
         (reage mais rapido a mudanca de banda que a media simples da P1).
      2. Margem por desvio-padrao -> subtrai k*sigma da estimativa. Quanto
         mais volatil a vazao (rede instavel), mais conservadora a escolha:
         evita escolher uma qualidade que so a media sustenta.
      3. Penalidade de jitter (com zona morta) -> jitter abaixo de jitter_floor
         e tratado como ruido normal da rede e NAO penaliza; so o excesso acima
         do piso reduz a estimativa. Quando o jitter EWMA sobe de verdade, a
         entrega fica irregular e a vazao media engana; a estimativa cai
         proporcionalmente ao excesso, protegendo o buffer. (Sem a zona morta,
         jitter saudavel de ~18ms ja cortava ~40% e a P3 perdia qualidade a toa.)

    Mantem histerese (assimetrica) e slow-start para nao reintroduzir a
    oscilacao que a P2 corrigiu: sobe devagar (confirm_up), desce rapido
    (confirm_down) para proteger o buffer quando a banda cai."""
    def __init__(self, representations, alpha=0.4, k_sigma=1.0, jitter_ref_ms=60.0,
                 jitter_floor_ms=20.0, jitter_cap=0.5, window=5, confirm_up=2, confirm_down=1):
        self.qualities = sorted(representations, key=lambda q: q["bitrate_kbps"])
        self.alpha = alpha
        self.k = k_sigma
        self.jitter_ref = jitter_ref_ms
        self.jitter_floor = jitter_floor_ms
        self.jitter_cap = jitter_cap
        self.window = window
        self.confirm_up = confirm_up
        self.confirm_down = confirm_down
        self.ewma = None
        self.samples = []
        self.jitter_ewma = 0.0
        self.current = 0 # slow-start: comeca na menor qualidade
        self.pending_target = None
        self.pending_count = 0
        self.last_estimate = 0.0
        self.last_reason = "slow-start"

    def update(self, throughput_kbps, jitter_ewma_ms=0.0):
        self.ewma = throughput_kbps if self.ewma is None else \
            self.alpha * throughput_kbps + (1 - self.alpha) * self.ewma
        self.samples.append(throughput_kbps)
        if len(self.samples) > self.window:
            self.samples.pop(0)
        self.jitter_ewma = jitter_ewma_ms

    def _estimate(self):
        """Retorna (estimativa, sigma, penalidade_jitter)."""
        sigma = statistics.pstdev(self.samples) if len(self.samples) >= 2 else 0.0
        pen = 1.0
        if self.jitter_ref > 0:
            excess = max(0.0, self.jitter_ewma - self.jitter_floor)  # zona morta: ignora jitter normal
            pen = 1 - min(excess / self.jitter_ref, self.jitter_cap)
        est = max(0.0, (self.ewma - self.k * sigma) * pen)
        return est, sigma, pen

    def _target_index(self, est):
        target = 0
        for i, q in enumerate(self.qualities):
            if q["bitrate_kbps"] <= est:
                target = i
        return target

    def select(self):
        if self.ewma is None:
            self.last_estimate = 0.0
            self.last_reason = "slow-start (sem historico) -> menor q"
            return self.qualities[self.current]
        est, sigma, pen = self._estimate()
        self.last_estimate = est
        target = self._target_index(est)
        if target == self.current:
            self.pending_count = 0 # ja estamos onde a estimativa pede
        else:
            confirm = self.confirm_up if target > self.current else self.confirm_down
            if target == self.pending_target:
                self.pending_count += 1
            else:
                self.pending_target = target
                self.pending_count = 1
            if self.pending_count >= confirm:
                self.current += 1 if target > self.current else -1 # um nivel por vez
                self.pending_count = 0
        q = self.qualities[self.current]
        self.last_reason = (f"ewma{self.ewma:.0f} -{self.k:g}σ{sigma:.0f} "
                            f"x{pen:.2f}jit = {est:.0f} -> {q['quality']}")
        return q

POLICIES = {"p1": RateBasedABR, "p2": RateBasedHysteresisABR, "p3": EwmaStdJitterABR}

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
        """Procura o proximo servidor saudavel por prioridade. Retorna True se migrou.
        Nao imprime: o painel/caller cuida da saida com o contexto de buffer."""
        t0 = time.time()
        from_id = self.current["id"]
        order = list(range(self.active + 1, len(self.servers))) + list(range(0, self.active))
        for idx in order:
            if health_check(self.servers[idx]["url"]):
                self.active = idx
                self.failovers += 1
                dt = time.time() - t0
                self.events.append((segment_idx, from_id, self.current["id"], dt))
                return True
        return False


# ---------------- painel ----------------

_ANSI = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "mag": "\033[35m", "cyan": "\033[36m",
}

class Panel:
    """Saida ao vivo no terminal (stdlib, sem deps). Uma linha por segmento,
    mantendo o historico visivel (responde 'em qual segmento o cliente detectou
    a mudanca'), com a razao da decisao ABR ao lado e os eventos (failover, rebuffer) em destaque."""
    def __init__(self, policy, color=None, verbose=True):
        self.policy = policy
        self.verbose = verbose
        self.color = sys.stdout.isatty() if color is None else color
        self.prev_thr = None
        self.prev_bitrate = None

    def _p(self, text, *codes):
        if not self.color or not codes:
            return text
        return "".join(_ANSI[c] for c in codes) + text + _ANSI["reset"]

    def header(self, servers, qualities, seg_dur, extra=""):
        bar = "═" * 92
        print(self._p(bar, "cyan"))
        print(self._p(f" ABR - política {self.policy.upper()} ", "bold", "cyan")
              + self._p(f" servidores={servers}  qualidades={qualities}  seg={seg_dur}s", "dim"))
        if extra:
            print(self._p(" " + extra, "dim"))
        print(self._p(bar, "cyan"))

    def segment(self, i, server_id, quality, bitrate, thr, buf, can_play, jitter, reason):
        if not self.verbose:
            self.prev_thr, self.prev_bitrate = thr, bitrate
            return
        if self.prev_thr is None or abs(thr - self.prev_thr) < 0.05 * max(self.prev_thr, 1):
            trend = self._p("=", "dim")
        elif thr > self.prev_thr:
            trend = self._p("▲", "green")
        else:
            trend = self._p("▼", "red")
        sw = " "
        if self.prev_bitrate is not None and bitrate != self.prev_bitrate:
            sw = self._p("↑", "bold", "green") if bitrate > self.prev_bitrate else self._p("↓", "bold", "yellow")
        self.prev_thr, self.prev_bitrate = thr, bitrate

        srv = self._p(f"{server_id}", "green" if server_id == "A" else "yellow")
        buf_s = self._p(f"{buf:4.1f}", "green" if can_play else "red")
        play = self._p("✓", "green") if can_play else self._p("✗", "red")
        jit_s = self._p(f"{jitter:4.0f}⚠", "yellow") if jitter >= 40 else f"{jitter:4.0f} "
        q_s = self._p(f"{quality:>5}", "bold")
        print(f"seg {i:3d} │ {srv} │ {q_s} {bitrate:4d}{sw}│ thr {thr:5.0f}{trend} │ "
              f"buf {buf_s}{play} │ jit {jit_s} │ " + self._p(reason, "dim"))

    def failover(self, seg, frm, to, dt_ms, buf, can_play):
        verdict = "buffer absorveu, sem stall" if can_play else "BUFFER INSUFICIENTE - rebuffer"
        print(self._p(f"  ⚠ FAILOVER  {frm}→{to}  no seg {seg}  "
                      f"({dt_ms:.1f} ms p/ achar servidor saudável)  "
                      f"buffer {buf:.1f}s → {verdict}", "bold", "red"))

    def rebuffer(self, seg, stall, buf):
        print(self._p(f"  ✗ REBUFFER  seg {seg}  stall {stall:.2f}s  (buffer esgotou)", "bold", "red"))

    def summary(self, stats):
        rows = [
            ("segmentos", f"{stats['segments']}"),
            ("trocas de qualidade", f"{stats['switches']}"),
            ("rebuffers", f"{stats['rebuffers']}"),
            ("stall total", f"{stats['stall']:.2f} s"),
            ("bitrate médio", f"{stats['avg_bitrate']:.0f} kbps"),
            ("failovers", f"{stats['failovers']}" + (f"   {stats['fo_detail']}" if stats['fo_detail'] else "")),
        ]
        label_w = max(len(k) for k, _ in rows)
        title = f" RESUMO {self.policy.upper()} "
        body_w = max(label_w + 2 + max(len(v) for _, v in rows), len(title))
        print(self._p("┌" + title + "─" * (body_w + 2 - len(title)) + "┐", "bold", "cyan"))
        for k, v in rows:
            body = f"{k:<{label_w}}  {v}".ljust(body_w)
            print(self._p("│ ", "cyan") + body + self._p(" │", "cyan"))
        print(self._p("└" + "─" * (body_w + 2) + "┘", "cyan"))


def parse_args():
    p = argparse.ArgumentParser(description="Cliente ABR com failover (P1 baseline / P2 histerese / P3 EWMA+sigma+jitter)")
    p.add_argument("--server", default=DEFAULT_SERVER, help="URL base do servidor de manifest")
    p.add_argument("--policy", choices=list(POLICIES), default="p1", help="politica ABR")
    p.add_argument("--confirm", type=int, default=3, help="P2: segmentos para confirmar subida")
    p.add_argument("--alpha", type=float, default=0.4, help="P3: peso da EWMA de vazao")
    p.add_argument("--k-sigma", type=float, default=1.0, help="P3: margem conservadora (k desvios-padrao)")
    p.add_argument("--jitter-ref", type=float, default=60.0, help="P3: jitter (ms) acima do piso que satura a penalidade")
    p.add_argument("--jitter-floor", type=float, default=20.0, help="P3: jitter (ms) tratado como ruido normal (zona morta, sem penalidade)")
    p.add_argument("--max-buffer", type=float, default=10.0, help="teto do buffer em s (playback real-time)")
    p.add_argument("-n", "--segments", type=int, default=DEFAULT_SEGMENTS, help="Numero de segmentos")
    p.add_argument("-o", "--output", default=DEFAULT_CSV, help="Arquivo CSV de saida")
    p.add_argument("--quiet", action="store_true", help="sem painel por segmento (so eventos + resumo); usado nas runs em lote")
    p.add_argument("--no-color", action="store_true", help="desliga cores ANSI")
    return p.parse_args()

def make_abr(policy, representations, args):
    if policy == "p3":
        return EwmaStdJitterABR(representations, alpha=args.alpha, k_sigma=args.k_sigma,
                                jitter_ref_ms=args.jitter_ref, jitter_floor_ms=args.jitter_floor)
    if policy == "p2":
        return RateBasedHysteresisABR(representations, confirm=args.confirm)
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
    seg_dur = float(manifest.get("segment_duration_s", 2.0))
    servers = resolve_servers(manifest, args.server)

    buf = BufferManager(seg_dur, max_buffer=args.max_buffer)
    abr = make_abr(args.policy, representations, args)
    fo = FailoverController(servers)
    jitter_ewma = 0.0
    alpha = 0.2

    panel = Panel(args.policy, color=(False if args.no_color else None), verbose=not args.quiet)
    panel.header([s["id"] for s in servers], [q["quality"] for q in representations], seg_dur)

    bitrates = []
    stall_total = 0.0
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

            failovers_before = fo.failovers
            elapsed = thr = jitter_net = None
            while True:
                url = f"{fo.current['url']}{q['url_path']}"
                try:
                    elapsed, thr, jitter_net = download_segment(url)
                    break
                except NET_ERRORS:
                    if not fo.failover(i):
                        print("Nenhum servidor saudavel. Interrompendo.")
                        break
            if elapsed is None:
                break

            stall = buf.consume()   # contabiliza o playback durante o download/failover
            buf.add_segment()
            jitter_ewma = alpha * jitter_net + (1 - alpha) * jitter_ewma
            abr.update(thr, jitter_ewma)
            rebuffer = 1 if stall > 0 else 0
            can_play = buf.can_play()
            bitrates.append(q["bitrate_kbps"])
            stall_total += stall
            w.writerow([
                i, datetime.now().isoformat(), fo.current["id"], q["quality"], q["bitrate_kbps"],
                round(thr, 2), round(elapsed, 3), round(jitter_net, 2),
                round(jitter_ewma, 2), round(buf.level, 2), can_play,
                rebuffer, round(stall, 3), fo.failovers
            ])
            f.flush() # uma linha por segmento ja persistida: correlacao com Wireshark / nao perde dados se o cliente for morto

            panel.segment(i, fo.current["id"], q["quality"], q["bitrate_kbps"], thr, buf.level, can_play, jitter_net, abr.last_reason)
            if fo.failovers > failovers_before:
                _, frm, to, dt = fo.events[-1]
                panel.failover(i, frm, to, dt * 1000, buf.level, can_play)
            if rebuffer:
                panel.rebuffer(i, stall, buf.level)

    switches = sum(1 for j in range(1, len(bitrates)) if bitrates[j] != bitrates[j - 1])
    fo_detail = ", ".join(f"{a}→{b} @seg{seg}" for seg, a, b, _ in fo.events)
    panel.summary({
        "segments": len(bitrates),
        "switches": switches,
        "rebuffers": buf.rebuffer_events,
        "stall": round(stall_total, 2),
        "avg_bitrate": (sum(bitrates) / len(bitrates)) if bitrates else 0,
        "failovers": fo.failovers,
        "fo_detail": fo_detail,
    })
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
