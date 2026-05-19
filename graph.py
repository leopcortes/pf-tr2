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

def plot_throughput_quality(rows, out):
    segs = [int(r["segment"]) for r in rows]
    thr = [float(r["throughput_kbps"]) for r in rows]
    bitrate = [int(r["bitrate_kbps"]) for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(segs, thr, label="Vazão medida (kbps)", color="tab:blue")
    ax.plot(segs, bitrate, label="Bitrate selecionado (kbps)", color="tab:red", linestyle="--")
    ax.set_xlabel("Segmento")
    ax.set_ylabel("kbps")
    ax.set_title("Vazão x Qualidade selecionada - Baseline (Rate-Based)")
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
    ax.set_xlabel("Segmento")
    ax.set_ylabel("Segundos")
    ax.set_title("Nível do buffer e eventos de rebuffering")
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
    ax.set_xlabel("Segmento")
    ax.set_ylabel("ms")
    ax.set_title("Variação de atraso (jitter)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def main():
    p = argparse.ArgumentParser(description="Gera gráficos a partir do CSV do cliente baseline")
    p.add_argument("-i", "--input", default="metrics_baseline.csv")
    p.add_argument("-d", "--outdir", default=".")
    args = p.parse_args()

    rows = load(args.input)
    if not rows:
        print("CSV vazio.")
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    plot_throughput_quality(rows, os.path.join(args.outdir, "throughput_quality.png"))
    plot_buffer(rows, os.path.join(args.outdir, "buffer_level.png"))
    plot_jitter(rows, os.path.join(args.outdir, "jitter.png"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
