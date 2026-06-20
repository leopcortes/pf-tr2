import argparse
import csv
import os

import matplotlib.pyplot as plt

def load(path):
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows

def failover_segments(rows):
    """Segmentos onde failover_total incrementou (evento de troca de servidor)."""
    segs = []
    prev = 0
    for r in rows:
        total = int(r.get("failover_total", 0) or 0)
        if total > prev:
            segs.append(int(r["segment"]))
        prev = total
    return segs

def mark_failovers(ax, segs, label="failover"):
    for k, s in enumerate(segs):
        ax.axvline(s, color="black", linestyle="-.", alpha=0.7,
                   label=label if k == 0 else None)

def plot_throughput_quality(rows, out):
    segs = [int(r["segment"]) for r in rows]
    thr = [float(r["throughput_kbps"]) for r in rows]
    bitrate = [int(r["bitrate_kbps"]) for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(segs, thr, label="Vazao medida (kbps)", color="tab:blue")
    ax.plot(segs, bitrate, label="Bitrate selecionado (kbps)", color="tab:red", linestyle="--")
    mark_failovers(ax, failover_segments(rows))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("kbps")
    ax.set_title("Vazao x Qualidade selecionada")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def plot_buffer(rows, out):
    segs = [int(r["segment"]) for r in rows]
    buf = [float(r["buffer_level_s"]) for r in rows]
    rebuf = [int(r["rebuffer_event"]) for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(segs, buf, label="Nivel do buffer (s)", color="tab:green")
    ax.axhline(2.0, color="gray", linestyle=":", label="threshold can_play=2s")
    rb_segs = [s for s, r in zip(segs, rebuf) if r == 1]
    if rb_segs:
        ax.scatter(rb_segs, [0] * len(rb_segs), marker="x", color="red", s=80, label="rebuffer")
    mark_failovers(ax, failover_segments(rows))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("Segundos")
    ax.set_title("Nivel do buffer e eventos de rebuffering")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def plot_jitter(rows, out):
    segs = [int(r["segment"]) for r in rows]
    jnet = [float(r["jitter_network_ms"]) for r in rows]
    jew = [float(r["jitter_ewma_ms"]) for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(segs, jnet, label="Jitter por segmento (ms)", color="tab:gray", alpha=0.6)
    ax.plot(segs, jew, label="Jitter EWMA (ms)", color="tab:purple", linewidth=2)
    mark_failovers(ax, failover_segments(rows))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("ms")
    ax.set_title("Variacao de atraso (jitter)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

# ---- modo comparacao (N politicas no mesmo cenario, ex: P1 vs P2 vs P3) ----

COLORS = ["tab:red", "tab:green", "tab:blue", "tab:orange", "tab:purple"]

def _col(rows, name, cast=float):
    return [cast(r[name]) for r in rows]

def _all_failovers(series):
    segs = []
    for rows, _ in series:
        segs += failover_segments(rows)
    return segs

def compare_quality(series, out):
    """series = [(rows, label), ...]. Bitrate de cada politica + vazao de
    referencia (cenario igual para todas)."""
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ref_rows = series[0][0]
    ax.plot(_col(ref_rows, "segment", int), _col(ref_rows, "throughput_kbps"),
            color="tab:gray", alpha=0.4, linewidth=1, label="Vazao medida (kbps)")
    for i, (rows, label) in enumerate(series):
        ax.step(_col(rows, "segment", int), _col(rows, "bitrate_kbps", int), where="post",
                color=COLORS[i % len(COLORS)], linewidth=2, label=f"Bitrate {label}")
    mark_failovers(ax, _all_failovers(series))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("kbps")
    ax.set_title("Qualidade selecionada por politica (mesmo cenario, mesma escala)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def compare_buffer(series, out):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.axhline(2.0, color="gray", linestyle=":", label="threshold can_play=2s")
    for i, (rows, label) in enumerate(series):
        color = COLORS[i % len(COLORS)]
        ax.plot(_col(rows, "segment", int), _col(rows, "buffer_level_s"),
                color=color, linewidth=2, label=f"Buffer {label}")
        rb = [int(r["segment"]) for r in rows if int(r["rebuffer_event"]) == 1]
        if rb:
            ax.scatter(rb, [0] * len(rb), marker="x", color=color, s=80,
                       zorder=5, label=f"rebuffer {label}")
    mark_failovers(ax, _all_failovers(series))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("Segundos")
    ax.set_title("Nivel do buffer e rebuffering por politica")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def compare_jitter(series, out):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for i, (rows, label) in enumerate(series):
        ax.plot(_col(rows, "segment", int), _col(rows, "jitter_ewma_ms"),
                color=COLORS[i % len(COLORS)], linewidth=2, label=f"Jitter EWMA {label}")
    mark_failovers(ax, _all_failovers(series))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("ms")
    ax.set_title("Variacao de atraso (jitter) EWMA por politica")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def main():
    p = argparse.ArgumentParser(description="Graficos do cliente ABR (individual ou comparativo N politicas)")
    p.add_argument("-i", "--input", default="metrics_baseline.csv")
    p.add_argument("--compare", action="append", default=[],
                   help="CSV adicional para sobrepor; repita para 3+ politicas (P1 vs P2 vs P3)")
    p.add_argument("--labels", nargs="+", help="rotulos das series (1 por CSV, incluindo o -i)")
    p.add_argument("--label1", default="P1")  # retrocompat
    p.add_argument("--label2", default="P2")
    p.add_argument("--no-jitter", action="store_true", help="nao gerar o grafico de jitter")
    p.add_argument("-d", "--outdir", default=".")
    args = p.parse_args()

    rows = load(args.input)
    if not rows:
        print("CSV vazio.")
        return 1
    os.makedirs(args.outdir, exist_ok=True)

    if args.compare:
        paths = [args.input] + args.compare
        labels = args.labels if args.labels else [args.label1, args.label2] + \
            [f"P{i + 3}" for i in range(len(args.compare) - 1)]
        series = []
        for path, label in zip(paths, labels):
            r = load(path)
            if not r:
                print(f"CSV de comparacao vazio: {path}")
                return 1
            series.append((r, label))
        compare_quality(series, os.path.join(args.outdir, "compare_quality.png"))
        compare_buffer(series, os.path.join(args.outdir, "compare_buffer.png"))
        if not args.no_jitter:
            compare_jitter(series, os.path.join(args.outdir, "compare_jitter.png"))
        return 0

    plot_throughput_quality(rows, os.path.join(args.outdir, "throughput_quality.png"))
    plot_buffer(rows, os.path.join(args.outdir, "buffer_level.png"))
    if not args.no_jitter:
        plot_jitter(rows, os.path.join(args.outdir, "jitter.png"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
