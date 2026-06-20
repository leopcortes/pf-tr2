# TODO - Entrega Final TR2

- [ ] Relatório final em PDF
  - [ ] 1. Introdução e contextualização
  - [ ] 2. Arquitetura (cliente, buffer manager, failover)
  - [ ] 3. Baseline: descrição + pseudocódigo + gráficos
  - [ ] 4. Deficiências do baseline com números dos CSVs (oscilação: 7 trocas)
  - [ ] 5. Política 2: motivação, descrição, comparação com baseline
  - [ ] 6. Política 3: componente estatístico/jitter justificado + resultados
  - [ ] 7. Failover: timestamp do evento, buffer no momento, impacto na qualidade
  - [ ] 8. Wireshark: prints anotados com correlação TCP ↔ CSV
  - [ ] 9. Discussão: qual política ganhou e por quê (com dados)
  - [ ] 10. Conclusão

- [ ] Captura Wireshark do FAILOVER
  - [ ] Capturar `tcp.port == 8080` durante uma sessão com failover
  - [ ] Identificar RST/FIN do servidor A + SYN para o servidor B
  - [ ] Correlacionar o timestamp do print com a linha do CSV onde `server_id` muda
  - [ ] Print anotado para a seção 8 do relatório

- [ ] Enviar arquvios no aprender
  - [ ] CSVs como anexo (`results//metrics*.csv` + `sessao.csv`)
  - [ ] Gráficos finais (`results//*.png`)
  - [ ] Código: `client.py` e `README.md`
