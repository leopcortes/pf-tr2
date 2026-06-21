# TODO - Entrega Final TR2

## 1. Relatório final
- [ ] 1. Introdução e contextualização
- [ ] 2. Arquitetura (cliente, buffer manager, failover)  <!-- README.md: "Arquitetura"/"Buffer e pacing" -->
- [ ] 3. Baseline: descrição + pseudocódigo + gráficos    <!-- usar results/p1_baseline/ -->
- [ ] 4. Deficiências do baseline com números dos CSVs (oscilação: P1 = 7 trocas)  <!-- results/p2_controlled/ -->
- [ ] 5. Política 2: motivação, descrição, comparação com baseline  <!-- README.md "Política 2" + results/p2_controlled/ -->
- [ ] 6. Política 3: componente estatístico/jitter justificado + resultados  <!-- README.md "Política 3" + results/p3_jitter/ -->
- [ ] 7. Failover: timestamp do evento, buffer no momento, impacto na qualidade  <!-- results/p2_failover/ + results/sessao.csv -->
- [ ] 8. Wireshark: prints anotados com correlação TCP ↔ CSV  <!-- depende da captura abaixo -->
- [ ] 9. Discussão: qual política ganhou e por quê (com dados + QoE)  <!-- README.md "Conclusão (com dados)" -->
- [ ] 10. Conclusão

## 2. Captura Wireshark do FAILOVER
<!-- comando para criar o cenário de failover está no Comandos.md seção 1 (mock) e seção 3 (ensaio live) -->
- [ ] Capturar `tcp.port == 8080` durante uma sessão COM failover
- [ ] Identificar RST/FIN do servidor A + SYN para o servidor B
- [ ] Correlacionar o timestamp do print com a linha do CSV onde `server_id` muda de A para B
- [ ] Print anotado para a seção 8 do relatório

## 3. Mapa dos arquivos de `results/`

| Pasta / arquivo | O que é | Onde usar no relatório |
|---|---|---|
| p1_baseline/ | P1 no servidor REAL, rede estável (Entrega 1) | |
| ├ `metrics.csv` | CSV da sessão baseline | Seção 3 (baseline) + anexo |
| ├ `throughput_quality.png` | vazão × qualidade do baseline | Seção 3 |
| └ `wireshark_baseline.png` | captura TCP do baseline | Seção 8 |
| p2_controlled/ | P1 vs P2 vs P3 no mock, banda ruidosa (oscilação) | |
| ├ `metrics_p{1,2,3}.csv` | CSVs das 3 políticas, mesmo cenário | Seções 4, 5, 9 + anexo |
| ├ `compare_quality.png` | qualidade das 3 políticas (P1 oscila) | Seção 4 (deficiência) e 5 |
| ├ `compare_buffer.png` | buffer das 3 políticas | Seção 5 |
| └ `wireshark_mock.png` | captura TCP no mock | Seção 8 |
| p3_jitter/ | P1 vs P2 vs P3 no mock, queda brusca + jitter | |
| ├ `metrics_p{1,2,3}.csv` | CSVs das 3 políticas | Seções 6, 9 + anexo |
| ├ `compare_quality.png` | qualidade (P3 sobrevive à queda) | Seção 6 |
| ├ `compare_buffer.png` | buffer + rebuffers (P2 colapsa) | Seção 6 (prova da P3) |
| └ `compare_jitter.png` | jitter EWMA das 3 políticas | Seção 6 (gráfico de jitter obrigatório) |
| p2_failover/ | P3 no mock com A derrubado → failover B | |
| ├ `metrics.csv` | CSV com o evento de failover | Seção 7 + anexo |
| ├ `throughput_quality.png` | vazão × qualidade, linha no failover | Seção 7 |
| └ `buffer_level.png` | buffer absorvendo a troca, linha no failover | Seção 7 |
| sessao.csv | sessão real da apresentação (gerar no dia) | Seções 7 e 9 + anexo |
| p1_real / p2_real / p3_real.csv | runs reais por política (demonstração) | opcional (anexo/ilustração) |
| prints/ `execucao_*.png` | screenshots do painel rodando | opcional (slides), NÃO obrigatório |
| live/ | ENSAIO do cenário surpresa (scratch) | NÃO entra no relatório |

<!-- gráficos obrigatórios do enunciado e onde estão:
     1) vazão+qualidade          -> p1_baseline/throughput_quality.png e p2_failover/throughput_quality.png
     2) buffer + rebuffers        -> p2_failover/buffer_level.png e p3_jitter/compare_buffer.png
     3) jitter EWMA               -> p3_jitter/compare_jitter.png
     4) 3 políticas lado a lado   -> p2_controlled/compare_quality.png e p3_jitter/compare_quality.png
     5) linha vertical no failover-> p2_failover/*.png (e sessao se houver failover ao vivo) -->

---

## 4. Entregar no Aprender

- [ ] Relatório final em PDF
- [ ] CSVs como anexo: `results//metrics*.csv` + `results/sessao.csv` (`p1_baseline`, `p2_controlled` ×3, `p3_jitter` ×3, `p2_failover`, `sessao`)
- [ ] Código: `client.py`, `graph.py`
- [ ] Guias junto do código: `README.md` e `Comandos.md`
