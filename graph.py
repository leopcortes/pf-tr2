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

# ---- modo comparacao (P1 vs P2 no mesmo cenario) ----

def _col(rows, name, cast=float):
    return [cast(r[name]) for r in rows]

def compare_quality(r1, r2, l1, l2, out):
    segs = _col(r1, "segment", int)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    # vazao medida como referencia compartilhada (cenario igual)
    ax.plot(segs, _col(r1, "throughput_kbps"), color="tab:blue", alpha=0.35,
            linewidth=1, label="Vazao medida (kbps)")
    ax.step(segs, _col(r1, "bitrate_kbps", int), where="post", color="tab:red",
            linewidth=2, label=f"Bitrate {l1}")
    ax.step(_col(r2, "segment", int), _col(r2, "bitrate_kbps", int), where="post",
            color="tab:green", linewidth=2, label=f"Bitrate {l2}")
    mark_failovers(ax, failover_segments(r1) + failover_segments(r2))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("kbps")
    ax.set_title(f"Qualidade selecionada: {l1} vs {l2} (mesmo cenario)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def compare_buffer(r1, r2, l1, l2, out):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    s1 = _col(r1, "segment", int)
    s2 = _col(r2, "segment", int)
    ax.plot(s1, _col(r1, "buffer_level_s"), color="tab:red", linewidth=2, label=f"Buffer {l1}")
    ax.plot(s2, _col(r2, "buffer_level_s"), color="tab:green", linewidth=2, label=f"Buffer {l2}")
    ax.axhline(2.0, color="gray", linestyle=":", label="threshold can_play=2s")
    for rows, color, lab in ((r1, "tab:red", l1), (r2, "tab:green", l2)):
        rb = [int(r["segment"]) for r in rows if int(r["rebuffer_event"]) == 1]
        if rb:
            ax.scatter(rb, [0] * len(rb), marker="x", color=color, s=70,
                       label=f"rebuffer {lab}")
    mark_failovers(ax, failover_segments(r1) + failover_segments(r2))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("Segundos")
    ax.set_title(f"Nivel do buffer: {l1} vs {l2}")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def compare_jitter(r1, r2, l1, l2, out):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(_col(r1, "segment", int), _col(r1, "jitter_ewma_ms"), color="tab:red",
            linewidth=2, label=f"Jitter EWMA {l1}")
    ax.plot(_col(r2, "segment", int), _col(r2, "jitter_ewma_ms"), color="tab:green",
            linewidth=2, label=f"Jitter EWMA {l2}")
    mark_failovers(ax, failover_segments(r1) + failover_segments(r2))
    ax.set_xlabel("Segmento")
    ax.set_ylabel("ms")
    ax.set_title(f"Jitter EWMA: {l1} vs {l2}")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"-> {out}")

def main():
    p = argparse.ArgumentParser(description="Graficos do cliente ABR (individual ou comparativo)")
    p.add_argument("-i", "--input", default="metrics_baseline.csv")
    p.add_argument("--compare", help="CSV da segunda politica para sobrepor (modo comparacao)")
    p.add_argument("--label1", default="P1")
    p.add_argument("--label2", default="P2")
    p.add_argument("-d", "--outdir", default=".")
    args = p.parse_args()

    rows = load(args.input)
    if not rows:
        print("CSV vazio.")
        return 1
    os.makedirs(args.outdir, exist_ok=True)

    if args.compare:
        rows2 = load(args.compare)
        if not rows2:
            print("CSV de comparacao vazio.")
            return 1
        compare_quality(rows, rows2, args.label1, args.label2,
                        os.path.join(args.outdir, "compare_quality.png"))
        compare_buffer(rows, rows2, args.label1, args.label2,
                       os.path.join(args.outdir, "compare_buffer.png"))
        compare_jitter(rows, rows2, args.label1, args.label2,
                       os.path.join(args.outdir, "compare_jitter.png"))
        return 0

    plot_throughput_quality(rows, os.path.join(args.outdir, "throughput_quality.png"))
    plot_buffer(rows, os.path.join(args.outdir, "buffer_level.png"))
    plot_jitter(rows, os.path.join(args.outdir, "jitter.png"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
