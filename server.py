"""Mock da infraestrutura da disciplina (Servidor A/B) para testes reprodutiveis.

Replica a interface observada no servidor real 137.131.178.229:
  GET /manifest         -> manifest v2.0 (lista de servidores + representacoes)
  GET /health           -> {status, instance, bandwidth_kbps, jitter_ms, uptime_s}
  GET /segment/<quality>-> bytes do segmento com rate limiting + jitter programaticos
  GET /control?...      -> ajusta bandwidth_kbps / jitter_ms ao vivo; reset=1 zera o contador

Diferente do real, ESTE mock e killable e a banda pode variar por indice de
segmento (--profile), garantindo que P1 e P2 enfrentem o MESMO cenario.

Uso:
  python3 server.py --id A      --port 8080 --bandwidth 2000
  python3 server.py --id B  --port 8081 --bandwidth 1000
  python3 server.py --id A --port 8080 --profile "0:2000,8:400,16:1500,24:2200"
"""
import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SEGMENT_DURATION_S = 2

# segment_bytes = bitrate_kbps * seg_dur / 8 * 1000  (conteudo de SEGMENT_DURATION_S
# segundos no bitrate nominal). Assim baixar um segmento custa ~seg_dur de banda no
# bitrate escolhido: o buffer so cresce quando banda > bitrate, e drena (-> rebuffer)
# quando banda < bitrate. E o que torna a decisao ABR relevante para o buffer.
def _seg_bytes(bitrate_kbps):
    return int(bitrate_kbps * SEGMENT_DURATION_S / 8 * 1000)

REPRESENTATIONS = [
    {"quality": "240p",  "bitrate_kbps": 200,  "segment_bytes": _seg_bytes(200),  "url_path": "/segment/240p"},
    {"quality": "360p",  "bitrate_kbps": 400,  "segment_bytes": _seg_bytes(400),  "url_path": "/segment/360p"},
    {"quality": "480p",  "bitrate_kbps": 700,  "segment_bytes": _seg_bytes(700),  "url_path": "/segment/480p"},
    {"quality": "720p",  "bitrate_kbps": 1500, "segment_bytes": _seg_bytes(1500), "url_path": "/segment/720p"},
    {"quality": "1080p", "bitrate_kbps": 3000, "segment_bytes": _seg_bytes(3000), "url_path": "/segment/1080p"},
]
CHUNK = 4096

def parse_profile(spec):
    """'0:2000,8:400' -> [(0, 2000.0), (8, 400.0)] ordenado por indice de segmento."""
    if not spec:
        return []
    steps = []
    for part in spec.split(","):
        idx, bw = part.split(":")
        steps.append((int(idx), float(bw)))
    return sorted(steps, key=lambda s: s[0])

class ServerState:
    def __init__(self, instance, bandwidth, jitter, profile, seed=42, bw_noise=0.0):
        self.instance = instance
        self.bandwidth_kbps = float(bandwidth)
        self.jitter_ms = float(jitter)
        self.profile = profile
        self.seed = seed
        self.bw_noise = bw_noise
        self.segments_served = 0
        self.start = time.time()
        self.lock = threading.Lock()

    def bandwidth_for_next(self):
        """Banda/jitter do proximo segmento. Deterministico por indice (seed),
        para que P1 e P2 enfrentem exatamente o mesmo cenario."""
        with self.lock:
            idx = self.segments_served
            if self.profile:
                bw = self.profile[0][1]
                for threshold, value in self.profile:
                    if idx >= threshold:
                        bw = value
                self.bandwidth_kbps = bw
            bw = self.bandwidth_kbps
            if self.bw_noise > 0:
                rng = random.Random((self.seed, idx))
                bw *= 1 + rng.uniform(-self.bw_noise, self.bw_noise)
            self.segments_served += 1
            return bw, self.jitter_ms, idx

def build_manifest(host, port_a, port_b, state_bw, priority_a=1, priority_b=2):
    return {
        "version": "2.0",
        "segment_duration_s": SEGMENT_DURATION_S,
        "servers": [
            {"id": "A",     "url": f"http://{host}:{port_a}", "priority": priority_a,
             "bandwidth_kbps": state_bw, "jitter_ms": 0},
            {"id": "B", "url": f"http://{host}:{port_b}", "priority": priority_b,
             "bandwidth_kbps": None, "jitter_ms": None},
        ],
        "representations": REPRESENTATIONS,
    }

def make_handler(state, manifest):
    rep_by_quality = {r["quality"]: r for r in REPRESENTATIONS}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # silencia o log padrao; o experimento controla a saida

        def _json(self, payload, code=200):
            body = json.dumps(payload, indent=2).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/manifest":
                self._json(manifest)
            elif path == "/health":
                self._json({
                    "status": "ok",
                    "instance": state.instance,
                    "bandwidth_kbps": state.bandwidth_kbps,
                    "jitter_ms": state.jitter_ms,
                    "uptime_s": round(time.time() - state.start, 1),
                })
            elif path == "/control":
                q = parse_qs(parsed.query)
                if "reset" in q:
                    with state.lock:
                        state.segments_served = 0
                if "bandwidth_kbps" in q:
                    state.bandwidth_kbps = float(q["bandwidth_kbps"][0])
                if "jitter_ms" in q:
                    state.jitter_ms = float(q["jitter_ms"][0])
                self._json({"status": "ok", "bandwidth_kbps": state.bandwidth_kbps,
                            "jitter_ms": state.jitter_ms, "segments_served": state.segments_served})
            elif path.startswith("/segment/"):
                self._serve_segment(path.rsplit("/", 1)[-1])
            else:
                self._json({"error": "Not found"}, code=404)

        def _serve_segment(self, quality):
            rep = rep_by_quality.get(quality)
            if rep is None:
                self._json({"error": f"unknown quality {quality}"}, code=404)
                return
            bw_kbps, jitter_ms, idx = state.bandwidth_for_next()
            total = rep["segment_bytes"]
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Server-Instance", state.instance)
            self.send_header("Content-Length", str(total))
            self.end_headers()

            bps = bw_kbps * 1000.0
            sent = 0
            payload = b"\x00" * CHUNK
            jitter_s = jitter_ms / 1000.0
            jrng = random.Random((state.seed, idx, "jitter"))
            try:
                while sent < total:
                    n = min(CHUNK, total - sent)
                    delay = (n * 8) / bps if bps > 0 else 0.0
                    if jitter_s > 0:
                        delay += jrng.uniform(0, jitter_s)
                    time.sleep(delay)
                    self.wfile.write(payload[:n])
                    sent += n
            except (BrokenPipeError, ConnectionResetError):
                pass  # cliente desistiu (timeout/failover); ok

    return Handler

def main():
    p = argparse.ArgumentParser(description="Mock do servidor da disciplina (A/B)")
    p.add_argument("--id", default="A", help="id no manifest: A ou B")
    p.add_argument("--instance", default=None, help="rotulo no /health (default: A->A, B->B)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--port-a", type=int, default=8080, help="porta do A no manifest")
    p.add_argument("--port-b", type=int, default=8081, help="porta do B no manifest")
    p.add_argument("--bandwidth", type=float, default=2000.0, help="banda inicial em kbps")
    p.add_argument("--jitter", type=float, default=0.0, help="jitter em ms por chunk")
    p.add_argument("--profile", default="", help="banda por segmento, ex: '0:2000,8:400,16:1500'")
    p.add_argument("--bw-noise", type=float, default=0.0, help="ruido relativo na banda por segmento, ex: 0.18")
    p.add_argument("--seed", type=int, default=42, help="semente do ruido (reprodutibilidade)")
    args = p.parse_args()

    instance = args.instance or ("B" if args.id == "B" else args.id)
    state = ServerState(instance, args.bandwidth, args.jitter, parse_profile(args.profile),
                        seed=args.seed, bw_noise=args.bw_noise)
    manifest = build_manifest(args.host, args.port_a, args.port_b, args.bandwidth)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(state, manifest))
    print(f"[server {instance}] http://{args.host}:{args.port} "
          f"bw={args.bandwidth}kbps jitter={args.jitter}ms profile={args.profile or 'none'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[server {instance}] parando.")
        httpd.shutdown()

if __name__ == "__main__":
    main()
