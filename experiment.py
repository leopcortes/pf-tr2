"""Orquestrador dos experimentos da Entrega 2 (abordagem hibrida).

Tres cenarios:
  real        P1 vs P2 no servidor real da disciplina (rede estavel -> politicas
              praticamente equivalentes; serve de referencia "dado real").
  controlled  P1 vs P2 no mock com banda variavel -> a deficiencia (oscilacao)
              aparece e a P2 (histerese) a resolve. E a comparacao que prova o ponto.
  failover    derruba o servidor A (mock) no meio do streaming -> failover p/ B.
              So da pra fazer num servidor que controlamos (o real nao e killable).

Saida em results/{real,controlled}/{p1,p2,compare}/ e results/failover/.
Rode com o python do venv (matplotlib): .venv/bin/python experiment.py
"""
import argparse
import csv
import os
import subprocess
import sys
import time
import urllib.request

PY = sys.executable
HOST = "127.0.0.1"
# Cenario de banda (kbps) por indice de segmento:
#   0-7   1600  -> ramp up (P2 sobe ate 480p)
#   8+    1100  -> plateau ruidoso na fronteira do 480p: a vazao medida (~950) faz
#                  a estimativa (0.8x) cruzar 700 -> o baseline oscila 360p<->480p,
#                  a P2 (histerese) segura 480p.
DEFAULT_PROFILE = "0:1600,8:1100"


def wait_healthy(url, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def start_server(port, port_a, port_b, bandwidth, jitter, profile, bw_noise=0.0, seed=42):
    cmd = [PY, "server.py", "--id", "A" if port == port_a else "srv-B",
           "--host", HOST, "--port", str(port), "--port-a", str(port_a),
           "--port-b", str(port_b), "--bandwidth", str(bandwidth),
           "--jitter", str(jitter), "--bw-noise", str(bw_noise), "--seed", str(seed)]
    if profile:
        cmd += ["--profile", profile]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_healthy(f"http://{HOST}:{port}"):
        proc.terminate()
        raise RuntimeError(f"servidor na porta {port} nao subiu")
    return proc


def stop(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def run_client(policy, server_url, segments, output, confirm=3, max_buffer=10.0):
    subprocess.run([PY, "client.py", "--policy", policy, "--server", server_url,
                    "-n", str(segments), "-o", output, "--confirm", str(confirm),
                    "--max-buffer", str(max_buffer)], check=True)


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def metrics(rows):
    bitrates = [int(r["bitrate_kbps"]) for r in rows]
    switches = sum(1 for i in range(1, len(bitrates)) if bitrates[i] != bitrates[i - 1])
    rebuffers = sum(int(r["rebuffer_event"]) for r in rows)
    stall = sum(float(r["stall_duration_s"]) for r in rows)
    below = sum(1 for r in rows if int(r["buffer_can_play"]) == 0)
    avg_bitrate = sum(bitrates) / len(bitrates) if bitrates else 0
    return {
        "segmentos": len(rows),
        "trocas_qualidade": switches,
        "rebuffers": rebuffers,
        "stall_total_s": round(stall, 2),
        "seg_buffer_baixo": below,
        "bitrate_medio_kbps": round(avg_bitrate, 0),
    }


def print_table(m1, m2, l1="P1", l2="P2"):
    keys = ["segmentos", "trocas_qualidade", "rebuffers", "stall_total_s",
            "seg_buffer_baixo", "bitrate_medio_kbps"]
    width = max(len(k) for k in keys)
    print(f"{'metrica':<{width}} | {l1:>10} | {l2:>10}")
    print("-" * (width + 28))
    for k in keys:
        print(f"{k:<{width}} | {str(m1[k]):>10} | {str(m2[k]):>10}")
    delta = m1["trocas_qualidade"] - m2["trocas_qualidade"]
    print(f"\n-> P2 fez {delta} troca(s) de qualidade a menos que P1 "
          f"({'menos oscilacao' if delta > 0 else 'sem ganho' if delta == 0 else 'mais oscilacao'}).")


def graphs(outdir, *args):
    subprocess.run([PY, "graph.py", *args, "-d", outdir], check=True)


def csvp(d):
    return os.path.join(d, "metrics.csv")


def scen_dirs(outdir, name):
    """Cria results/<name>/{p1,p2,compare} e devolve os caminhos."""
    dirs = {sub: os.path.join(outdir, name, sub) for sub in ("p1", "p2", "compare")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs


def compare_pair(dirs, l1, l2, header):
    """Gera graficos individuais de P1/P2, imprime a tabela e o overlay."""
    c1, c2 = csvp(dirs["p1"]), csvp(dirs["p2"])
    graphs(dirs["p1"], "-i", c1)
    graphs(dirs["p2"], "-i", c2)
    print(f"\n=== Comparacao P1 vs P2 ({header}) ===")
    print_table(metrics(load(c1)), metrics(load(c2)), l1, l2)
    graphs(dirs["compare"], "-i", c1, "--compare", c2, "--label1", l1, "--label2", l2)


def run_controlled(args):
    """P1 vs P2 no mock com banda variavel: aqui a deficiencia (oscilacao) aparece."""
    print(f"\n### CONTROLADO (mock) | profile={args.profile} bw_noise={args.bw_noise}")
    dirs = args.controlled
    for policy in ("p1", "p2"):
        srv = start_server(args.port_a, args.port_a, args.port_b, 2000, args.jitter,
                           args.profile, bw_noise=args.bw_noise, seed=args.seed)
        try:
            print(f"\n--- {policy} (controlado) ---")
            run_client(policy, f"http://{HOST}:{args.port_a}", args.segments,
                       csvp(dirs[policy]), args.confirm, args.max_buffer)
        finally:
            stop(srv)
    compare_pair(dirs, "P1", "P2", "banda variavel")


def run_real(args):
    """P1 vs P2 no servidor real: rede estavel -> politicas praticamente equivalentes."""
    print(f"\n### REAL | servidor={args.real_server}")
    dirs = args.real
    for policy in ("p1", "p2"):
        print(f"\n--- {policy} (real) ---")
        run_client(policy, args.real_server, args.segments,
                   csvp(dirs[policy]), args.confirm, args.max_buffer)
    compare_pair(dirs, "P1 real", "P2 real", "rede estavel")


def run_failover(args):
    """Failover so e possivel num servidor que controlamos (o real nao da pra derrubar)."""
    print(f"\n### FAILOVER (mock, derruba A apos {args.kill_after}s)")
    csv_fo = os.path.join(args.dir_failover, "metrics.csv")
    srv_a = start_server(args.port_a, args.port_a, args.port_b, 1500, args.jitter, "")
    srv_b = start_server(args.port_b, args.port_a, args.port_b, 1000, args.jitter, "")
    try:
        client = subprocess.Popen(
            [PY, "client.py", "--policy", "p2", "--server", f"http://{HOST}:{args.port_a}",
             "-n", str(args.segments), "-o", csv_fo, "--confirm", str(args.confirm),
             "--max-buffer", str(args.max_buffer)])
        time.sleep(args.kill_after)
        print(f">> derrubando servidor A (porta {args.port_a})")
        stop(srv_a)
        client.wait()
    finally:
        stop(srv_a)
        stop(srv_b)
    rows = load(csv_fo)
    fo_rows = [r for r in rows if int(r["failover_total"]) > 0]
    if fo_rows:
        first = fo_rows[0]
        print(f"\n-> Failover no segmento {first['segment']}: server_id passou a {first['server_id']}, "
              f"qualidade {first['quality']}, buffer {first['buffer_level_s']}s, "
              f"can_play={first['buffer_can_play']} (1 = buffer absorveu a troca)")
    else:
        print("\n-> Nenhum failover registrado (A nao foi derrubado a tempo? aumente --kill-after).")
    graphs(args.dir_failover, "-i", csv_fo)


def main():
    p = argparse.ArgumentParser(description="Experimentos da Entrega 2 (P1 vs P2 + failover)")
    p.add_argument("--mode", choices=["real", "controlled", "failover", "all"], default="all")
    p.add_argument("--real-server", default="http://137.131.178.229:8080", help="servidor real da disciplina")
    p.add_argument("--profile", default=DEFAULT_PROFILE, help="banda por segmento (mock)")
    p.add_argument("--segments", type=int, default=30)
    p.add_argument("--confirm", type=int, default=3, help="P2: confirmacoes para mudar qualidade")
    p.add_argument("--jitter", type=float, default=2.0, help="jitter em ms por chunk no mock")
    p.add_argument("--bw-noise", type=float, default=0.22, help="ruido relativo da banda por segmento")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-buffer", type=float, default=20.0, help="teto do buffer (s)")
    p.add_argument("--port-a", type=int, default=8090)
    p.add_argument("--port-b", type=int, default=8091)
    p.add_argument("--kill-after", type=float, default=5.0, help="failover: segundos ate matar A")
    p.add_argument("--outdir", default="results")
    args = p.parse_args()

    args.real = scen_dirs(args.outdir, "real")
    args.controlled = scen_dirs(args.outdir, "controlled")
    args.dir_failover = os.path.join(args.outdir, "failover")
    os.makedirs(args.dir_failover, exist_ok=True)

    if args.mode in ("real", "all"):
        run_real(args)
    if args.mode in ("controlled", "all"):
        run_controlled(args)
    if args.mode in ("failover", "all"):
        run_failover(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
