"""Teste guiado da caixa: grava Y/rotação de todos os trackers OSC por fase."""
import csv
import os
import threading
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

FASES = [
    ("CHAO_INICIAL", 4, "Fique parado, dois pés no chão"),
    ("ESQ_NA_CAIXA", 6, "Coloque o pé ESQUERDO na caixa e fique parado"),
    ("CHAO_2", 4, "Volte os dois pés ao chão"),
    ("DIR_NA_CAIXA", 6, "Coloque o pé DIREITO na caixa e fique parado"),
    ("CHAO_3", 4, "Volte os dois pés ao chão"),
]

pos, rot = {}, {}


def handler(address, *args):
    p = address.split("/")
    if len(p) < 5:
        return
    if p[4] == "position" and len(args) == 3:
        pos[p[3]] = args
    elif p[4] == "rotation" and len(args) >= 3:
        rot[p[3]] = args


disp = Dispatcher()
disp.set_default_handler(handler)
srv = ThreadingOSCUDPServer(("127.0.0.1", 9000), disp)
threading.Thread(target=srv.serve_forever, daemon=True).start()

while not all(k in pos for k in ("1", "2", "3")):
    time.sleep(0.1)

ids = sorted(k for k in pos if k != "head")
linhas = []
t0 = time.time()
for nome, dur, msg in FASES:
    print(f"\n>>> {nome}: {msg} ({dur}s)")
    for i in range(3, 0, -1):
        print(f"    começa em {i}...", end="\r", flush=True)
        time.sleep(1)
    fim = time.time() + dur
    while time.time() < fim:
        row = [round(time.time() - t0, 3), nome]
        for k in ids:
            x, y, z = pos.get(k, (0, 0, 0))
            r = rot.get(k, (0, 0, 0))
            row += [round(y, 4), round(r[0], 2), round(r[1], 2), round(r[2], 2)]
        linhas.append(row)
        time.sleep(0.02)

os.makedirs("dados", exist_ok=True)
with open(os.path.join("dados", "teste_caixa.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    cab = ["t", "fase"]
    for k in ids:
        cab += [f"y{k}", f"rx{k}", f"ry{k}", f"rz{k}"]
    w.writerow(cab)
    w.writerows(linhas)
print("\nGravado em dados/teste_caixa.csv. Pode me avisar.")
srv.shutdown()

