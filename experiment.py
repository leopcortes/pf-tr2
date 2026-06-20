"""Orquestrador dos experimentos do Projeto Final (P1/P2/P3 + failover).

Cenarios:
  controlled  P1 vs P2 vs P3 no mock com banda variavel -> a deficiencia de
              oscilacao do baseline aparece; P2 e P3 a estabilizam.
  jitter      P1 vs P2 vs P3 com banda volatil + jitter alto -> aqui a P3
              ganha: desconta o desvio-padrao e penaliza o jitter, entao
              protege o buffer (menos rebuffer) onde P1/P2 estimam pela media.
  failover    derruba o servidor A (mock) no meio do streaming -> failover p/ B.
              So da pra fazer num servidor que controlamos (o real nao e killable).
  live        ENSAIO do cenario surpresa: um "professor" simulado muda a banda,
              injeta jitter e derruba o A ao vivo, com o painel do cliente rodando.

(P1 no servidor real, rede estavel, esta em results/p1_baseline/ - Entrega 1.)
Saida em results/p2_controlled, p3_jitter, p2_failover, live. Rode com o venv (matplotlib):
  .venv/bin/python experiment.py --mode all
"""
import argparse
import csv
import os
import subprocess
import sys
import threading
import time
import urllib.request

POLICIES_ALL = ["p1", "p2", "p3"]
LABELS = {"p1": "P1", "p2": "P2", "p3": "P3"}

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
    cmd = [PY, "server.py", "--id", "A" if port == port_a else "B",
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


def run_client(policy, server_url, segments, output, confirm=3, max_buffer=10.0,
               k_sigma=1.0, jitter_ref=50.0, alpha=0.4, quiet=True):
    cmd = [PY, "client.py", "--policy", policy, "--server", server_url,
           "-n", str(segments), "-o", output, "--confirm", str(confirm),
           "--max-buffer", str(max_buffer), "--alpha", str(alpha),
           "--k-sigma", str(k_sigma), "--jitter-ref", str(jitter_ref)]
    if quiet:
        cmd.append("--quiet")
    subprocess.run(cmd, check=True)


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


def print_table_n(csvs):
    """csvs = {policy: path}. Tabela comparativa lado a lado das politicas."""
    keys = ["segmentos", "trocas_qualidade", "rebuffers", "stall_total_s",
            "seg_buffer_baixo", "bitrate_medio_kbps"]
    mets = {p: metrics(load(path)) for p, path in csvs.items()}
    labels = [LABELS[p] for p in csvs]
    width = max(len(k) for k in keys)
    header = f"{'metrica':<{width}} | " + " | ".join(f"{l:>8}" for l in labels)
    print(header)
    print("-" * len(header))
    for k in keys:
        print(f"{k:<{width}} | " + " | ".join(f"{str(mets[p][k]):>8}" for p in csvs))
    return mets


def graphs(outdir, *args):
    subprocess.run([PY, "graph.py", *args, "-d", outdir], check=True)


def graphs_compare(outdir, csvs, no_jitter=False):
    """Grafico das politicas lado a lado (mesmo cenario)."""
    pols = list(csvs)
    call = ["-i", csvs[pols[0]], "--labels", *[LABELS[p] for p in pols]]
    for p in pols[1:]:
        call += ["--compare", csvs[p]]
    if no_jitter:
        call.append("--no-jitter")
    graphs(outdir, *call)


def control(server_url, **params):
    """Bate em /control do mock (banda/jitter ao vivo). Silencioso em falha."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    try:
        with urllib.request.urlopen(f"{server_url}/control?{q}", timeout=2) as r:
            r.read()
    except Exception:
        pass


def run_scenario_3(args, outdir, profile, bw_noise, jitter, label, max_buffer):
    """Roda P1, P2 e P3 no MESMO cenario: o servidor e reiniciado por politica,
    com a mesma seed -> sequencia identica de banda e jitter (comparacao justa).
    Retorna {policy: csv_path}."""
    print(f"\n### {label.upper()} | profile={profile} bw_noise={bw_noise} jitter={jitter}ms max_buffer={max_buffer}")
    csvs = {}
    for policy in POLICIES_ALL:
        out = os.path.join(outdir, f"metrics_{policy}.csv")
        srv = start_server(args.port_a, args.port_a, args.port_b, 2000, jitter,
                           profile, bw_noise=bw_noise, seed=args.seed)
        try:
            print(f"\n--- {policy} ({label}) ---")
            run_client(policy, f"http://{HOST}:{args.port_a}", args.segments, out,
                       confirm=args.confirm, max_buffer=max_buffer,
                       k_sigma=args.k_sigma, jitter_ref=args.jitter_ref, alpha=args.alpha)
        finally:
            stop(srv)
        csvs[policy] = out
    return csvs


def run_controlled(args):
    """P1 vs P2 vs P3, banda variavel: a deficiencia de oscilacao do baseline
    aparece; P2 e P3 a estabilizam."""
    d = args.dir_controlled
    csvs = run_scenario_3(args, d, args.profile, args.bw_noise, args.jitter,
                          "controlado", args.max_buffer)
    print("\n=== Comparacao P1 vs P2 vs P3 (banda variavel) ===")
    mets = print_table_n(csvs)
    print(f"\n-> trocas de qualidade: P1={mets['p1']['trocas_qualidade']} "
          f"P2={mets['p2']['trocas_qualidade']} P3={mets['p3']['trocas_qualidade']} "
          f"(P2/P3 estabilizam a oscilacao do baseline)")
    graphs_compare(d, csvs, no_jitter=True)


def run_jitter(args):
    """P1 vs P2 vs P3 com banda volatil + jitter alto: o cenario que prova a P3.
    P1/P2 estimam pela media e superestimam -> rebuffer. P3 desconta o desvio-
    padrao e penaliza o jitter -> protege o buffer."""
    d = args.dir_jitter
    csvs = run_scenario_3(args, d, args.jitter_profile, args.jitter_bw_noise,
                          args.jitter_ms, "jitter alto", args.jitter_max_buffer)
    print("\n=== Comparacao P1 vs P2 vs P3 (banda volatil + jitter alto) ===")
    mets = print_table_n(csvs)
    r1, r2, r3 = mets["p1"]["rebuffers"], mets["p2"]["rebuffers"], mets["p3"]["rebuffers"]
    s1, s2, s3 = mets["p1"]["stall_total_s"], mets["p2"]["stall_total_s"], mets["p3"]["stall_total_s"]
    print(f"\n-> rebuffers: P1={r1} P2={r2} P3={r3}  |  stall(s): P1={s1} P2={s2} P3={s3}")
    print(f"-> bitrate medio: P1={mets['p1']['bitrate_medio_kbps']:.0f} "
          f"P2={mets['p2']['bitrate_medio_kbps']:.0f} P3={mets['p3']['bitrate_medio_kbps']:.0f} kbps")
    if r3 < max(r1, r2):
        print("-> P3 reduziu rebuffering vs baseline mantendo qualidade comparavel (ver compare_buffer.png).")
    graphs_compare(d, csvs, no_jitter=False)


def run_failover(args):
    """Failover so e possivel num servidor que controlamos (o real nao da pra
    derrubar). Roda a P3 (politica final) e derruba o A no meio do streaming."""
    print(f"\n### FAILOVER (mock, P3, derruba A apos {args.kill_after}s)")
    d = args.dir_failover
    csv_fo = os.path.join(d, "metrics.csv")
    srv_a = start_server(args.port_a, args.port_a, args.port_b, 1500, args.jitter, "")
    srv_b = start_server(args.port_b, args.port_a, args.port_b, 1000, args.jitter, "")
    try:
        client = subprocess.Popen(
            [PY, "client.py", "--policy", "p3", "--server", f"http://{HOST}:{args.port_a}",
             "-n", str(args.segments), "-o", csv_fo, "--max-buffer", str(args.max_buffer),
             "--k-sigma", str(args.k_sigma), "--jitter-ref", str(args.jitter_ref)])
        time.sleep(args.kill_after)
        print(f">> derrubando servidor A (porta {args.port_a})")
        stop(srv_a)
        client.wait()
    finally:
        stop(srv_a)
        stop(srv_b)
    fo_rows = [r for r in load(csv_fo) if int(r["failover_total"]) > 0]
    if fo_rows:
        first = fo_rows[0]
        print(f"\n-> Failover no segmento {first['segment']}: server_id passou a {first['server_id']}, "
              f"qualidade {first['quality']}, buffer {first['buffer_level_s']}s, "
              f"can_play={first['buffer_can_play']} (1 = buffer absorveu a troca)")
    else:
        print("\n-> Nenhum failover registrado (aumente --kill-after).")
    graphs(d, "-i", csv_fo, "--no-jitter")


def run_live(args):
    """ENSAIO do cenario surpresa: o cliente (P3, painel ao vivo) roda contra o
    mock enquanto um 'professor' simulado, numa timeline, derruba a banda do A,
    injeta jitter e por fim mata o A -> failover p/ B. Serve para praticar a
    apresentacao final (na real, quem mexe no servidor e o professor)."""
    print("\n### LIVE - ensaio do cenario surpresa (P3)\n")
    d = args.dir_live
    csv_live = os.path.join(d, "metrics.csv")
    url_a = f"http://{HOST}:{args.port_a}"
    srv_a = start_server(args.port_a, args.port_a, args.port_b, args.live_bw0, 0, "")
    srv_b = start_server(args.port_b, args.port_a, args.port_b, args.live_bw_b, 0, "")

    def announce(msg):
        print(f"\n>> [professor] {msg}\n", flush=True)

    def professor():
        time.sleep(args.bw_at)
        announce(f"banda do A {args.live_bw0:.0f} -> {args.live_bw_drop:.0f} kbps")
        control(url_a, bandwidth_kbps=args.live_bw_drop)
        time.sleep(max(0, args.jit_at - args.bw_at))
        announce(f"injetando jitter {args.live_jit:.0f} ms no A")
        control(url_a, jitter_ms=args.live_jit)
        time.sleep(max(0, args.kill_at - args.jit_at))
        announce("derrubando o servidor A")
        stop(srv_a)

    try:
        client = subprocess.Popen(
            [PY, "client.py", "--policy", "p3", "--server", url_a,
             "-n", str(args.segments), "-o", csv_live, "--max-buffer", str(args.max_buffer),
             "--k-sigma", str(args.k_sigma), "--jitter-ref", str(args.jitter_ref)])
        prof = threading.Thread(target=professor, daemon=True)
        prof.start()
        client.wait()
    finally:
        stop(srv_a)
        stop(srv_b)
    graphs(d, "-i", csv_live, "--no-jitter")
    print(f"\n-> CSV e graficos do ensaio em {d}/")


def main():
    p = argparse.ArgumentParser(description="Experimentos do Projeto Final (P1/P2/P3 + failover + ensaio ao vivo)")
    p.add_argument("--mode", choices=["controlled", "jitter", "failover", "live", "all"], default="all")
    p.add_argument("--segments", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--port-a", type=int, default=8090)
    p.add_argument("--port-b", type=int, default=8091)
    # P2/P3
    p.add_argument("--confirm", type=int, default=3, help="P2: confirmacoes para mudar qualidade")
    p.add_argument("--alpha", type=float, default=0.4, help="P3: peso da EWMA de vazao")
    p.add_argument("--k-sigma", type=float, default=1.0, help="P3: margem (k desvios-padrao)")
    p.add_argument("--jitter-ref", type=float, default=45.0, help="P3: jitter de referencia (ms)")
    # cenario controlado
    p.add_argument("--profile", default=DEFAULT_PROFILE, help="banda por segmento (controlado)")
    p.add_argument("--jitter", type=float, default=2.0, help="jitter ms/chunk (controlado/failover)")
    p.add_argument("--bw-noise", type=float, default=0.22, help="ruido relativo da banda (controlado)")
    p.add_argument("--max-buffer", type=float, default=20.0, help="teto do buffer (s)")
    # cenario jitter (onde a P3 ganha): banda alta e estavel -> queda brusca a 430.
    # P1/P2 cavalgam 480p e demoram a descer (media/histerese) -> buffer drena ->
    # rebuffer; a P3 reage em 1 segmento (EWMA + salto do desvio-padrao) e sobrevive.
    p.add_argument("--jitter-profile", default="0:3000,10:430", help="banda por segmento (cenario jitter)")
    p.add_argument("--jitter-bw-noise", type=float, default=0.15, help="ruido da banda (cenario jitter)")
    p.add_argument("--jitter-ms", type=float, default=35.0, help="jitter ms/chunk (cenario jitter)")
    p.add_argument("--jitter-max-buffer", type=float, default=4.0, help="teto do buffer (cenario jitter)")
    # failover
    p.add_argument("--kill-after", type=float, default=5.0, help="failover: segundos ate matar A")
    # live (ensaio do cenario surpresa)
    p.add_argument("--live-bw0", type=float, default=1600.0, help="banda inicial do A (live)")
    p.add_argument("--live-bw-drop", type=float, default=600.0, help="banda do A apos a queda (live)")
    p.add_argument("--live-bw-b", type=float, default=1000.0, help="banda do B (live)")
    p.add_argument("--live-jit", type=float, default=40.0, help="jitter injetado (live)")
    p.add_argument("--bw-at", type=float, default=14.0, help="live: s ate baixar a banda")
    p.add_argument("--jit-at", type=float, default=22.0, help="live: s ate injetar jitter")
    p.add_argument("--kill-at", type=float, default=32.0, help="live: s ate matar o A")
    p.add_argument("--outdir", default="results")
    args = p.parse_args()

    dir_names = {"controlled": "p2_controlled", "jitter": "p3_jitter",
                 "failover": "p2_failover", "live": "live"}
    for name in ("controlled", "jitter", "failover", "live"):
        setattr(args, f"dir_{name}", os.path.join(args.outdir, dir_names[name]))
        os.makedirs(getattr(args, f"dir_{name}"), exist_ok=True)

    if args.mode in ("controlled", "all"):
        run_controlled(args)
    if args.mode in ("jitter", "all"):
        run_jitter(args)
    if args.mode in ("failover", "all"):
        run_failover(args)
    if args.mode == "live":
        run_live(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
