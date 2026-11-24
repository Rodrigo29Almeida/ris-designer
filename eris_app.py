"""
RIS Phase Distribution Calculator — versão Python (PySide6 + matplotlib) da app MATLAB
Theoretical_RIS_Phase_calculator.mlapp (v8).

Executar:  py -3.11 eris_app.py
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QGroupBox, QLabel, QDoubleSpinBox, QSpinBox, QCheckBox, QPushButton, QTabWidget,
                               QTableWidget, QTableWidgetItem, QHeaderView, QRadioButton, QButtonGroup, QComboBox,
                               QStackedWidget, QMessageBox, QProgressDialog, QSizePolicy, QAbstractItemView)

import ris_core as rc
import ris_cst          # o pywin32 só é carregado ao ligar ao CST

CORES = [(0, 0, 0), (0.85, 0.33, 0.10), (0.00, 0.45, 0.74), (0.47, 0.67, 0.19), (0.49, 0.18, 0.56)]
ESTILOS = ["-", "--", ":", "-.", "-"]
VERDE_OBJ = (0.11, 0.62, 0.46)
LARANJA_ESP = (0.85, 0.40, 0.20)


# ============================================================================ utilitários de interface
def figura(parent_layout=None, toolbar=True):
    """Widget com uma figura matplotlib (e barra de zoom/pan). Devolve (widget, fig)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    fig = Figure(layout="constrained")
    can = FigureCanvasQTAgg(fig)
    can.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    if toolbar:
        tb = NavigationToolbar2QT(can, w)
        tb.setIconSize(tb.iconSize() * 0.8)
        lay.addWidget(tb)
    lay.addWidget(can)
    if parent_layout is not None:
        parent_layout.addWidget(w)
    return w, fig


def xline(ax, x, estilo, texto, cor, lw=1.2):
    ax.axvline(x, linestyle=estilo, color=cor, lw=lw)
    if texto:
        ax.text(x, 0.98, " " + texto, transform=ax.get_xaxis_transform(), color=cor, rotation=90,
                va="top", ha="right", fontsize=8)


def tabela(dados, decimais=None, cabecalho=None, auto=False):
    dados = np.asarray(dados, dtype=object) if not isinstance(dados, list) else dados
    nl = len(dados)
    nc = len(dados[0]) if nl else 0
    t = QTableWidget(nl, nc)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    if cabecalho:
        t.setHorizontalHeaderLabels(cabecalho)
        t.verticalHeader().setVisible(False)
    else:
        t.setHorizontalHeaderLabels([str(j + 1) for j in range(nc)])
        t.setVerticalHeaderLabels([str(i + 1) for i in range(nl)])
    for i in range(nl):
        for j in range(nc):
            v = dados[i][j]
            if isinstance(v, (float, np.floating)):
                s = "NaN" if np.isnan(v) else (("%." + str(decimais) + "f") % v if decimais is not None else rc.g(v))
            else:
                s = str(v)
            it = QTableWidgetItem(s)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, j, it)
    if auto:
        t.resizeColumnsToContents()
    else:
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t


def barra(*widgets, stretch_last=True):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)
    for x in widgets:
        h.addWidget(x)
    if not stretch_last:
        h.addStretch(1)
    return w


def italico(txt):
    l = QLabel(txt)
    f = l.font(); f.setItalic(True); l.setFont(f)
    l.setWordWrap(True)
    return l


def negrito(txt):
    l = QLabel(txt)
    f = l.font(); f.setBold(True); l.setFont(f)
    l.setWordWrap(True)
    return l


def seletor(opcoes, atual):
    """Equivalente ao uiswitch: duas opções exclusivas. Devolve (widget, grupo)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    gr = QButtonGroup(w)
    for i, o in enumerate(opcoes):
        r = QRadioButton(o)
        r.setChecked(i == atual)
        gr.addButton(r, i)
        h.addWidget(r)
    return w, gr


def substituir(container_layout, novo):
    while container_layout.count():
        it = container_layout.takeAt(0)
        if it.widget():
            it.widget().hide()                  # esconde já (o deleteLater só corre no próximo ciclo)
            it.widget().deleteLater()
    container_layout.addWidget(novo)


# ============================================================================ CST numa thread (COM)
class TrabalhoCST(QThread):
    progresso = Signal(str)
    inicioCaso = Signal(int)            # índice (0..n-1) do trabalho que começou
    fimCaso = Signal(float, bool)       # duração [s], True = foi simulação (False = leitura de projeto)
    feito = Signal(list, list)          # [ValCST], [falhas]

    def __init__(self, R, jobs, assin, modelo="ibc"):
        """modelo: 'ibc' (impedância de superfície, I-solver) ou 'diel' (dielétrico real, FD solver)."""
        super().__init__()
        self.R, self.jobs, self.assin, self.modelo = R, jobs, assin, modelo

    def run(self):
        import pythoncom
        import ris_cst
        pythoncom.CoInitialize()
        ok, falhas = [], []
        try:
            n = len(self.jobs)
            for k, (caso, fich, simular) in enumerate(self.jobs, start=1):
                prog = (lambda t, k=k: self.progresso.emit(f"[{k}/{n}]  {t}")) if n > 1 else self.progresso.emit
                self.inicioCaso.emit(k - 1)
                t0 = time.time()
                try:
                    if simular and self.modelo == "diel":
                        v = ris_cst.validar_dielectrico(self.R, caso, prog)
                    elif simular:
                        v = ris_cst.validar(self.R, caso, prog)
                    else:
                        v = ris_cst.ler_existente(self.R, caso, fich, prog)
                    rc.gravar_mat(v, self.assin)            # para voltar a carregar sem abrir o CST
                    print(f"» CST ({caso}): {v.tempo:.0f} s  ->  {v.ficheiro}")
                    ok.append(v)
                except Exception as e:
                    print(f"» CST ({caso}) falhou: {e}", file=sys.stderr)
                    falhas.append(f"{caso}: {e}")
                self.fimCaso.emit(time.time() - t0, bool(simular))
        finally:
            pythoncom.CoUninitialize()
        self.feito.emit(ok, falhas)


# ============================================================================ janela principal
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RIS Phase Distribution Calculator")
        self.resize(1150, 1000)

        # estado que sobrevive entre cálculos (como os findobj(...) da app MATLAB)
        self.R = None
        self.usarDelta = True
        self.mostrarIdeal = False
        self.fonteMapa = 0              # Mapa 2D: 0 = App (teoria), 1 = CST (dielétrico)
        self.normalizar = True          # True: dB relativos ao contínuo; False: valores originais
        self.selInfo = self.selDiag = self.selMapa = None
        self.memCST = {"assinatura": None, "val": {}}     # IBC (Val_*): já não aparece na interface
        self.memDiel = {"assinatura": None, "val": {}}    # CST com o dielétrico real (ValDiel_*)
        self.worker = None

        central = QWidget()
        self.setCentralWidget(central)
        V = QVBoxLayout(central)

        # ---- RIS Characteristics
        gb = QGroupBox("RIS Characteristics:")
        f = gb.font(); f.setBold(True); gb.setFont(f)
        G = QGridLayout(gb)

        def campo(val, dec=4, mn=-1e9, mx=1e9, inteiro=False):
            w = QSpinBox() if inteiro else QDoubleSpinBox()
            w.setRange(int(mn), int(mx)) if inteiro else w.setRange(mn, mx)
            if not inteiro:
                w.setDecimals(dec)
            w.setValue(val)
            w.setFont(QFont())
            w.setMinimumWidth(80)
            return w

        self.M = campo(10, inteiro=True, mn=1, mx=200)
        self.N = campo(10, inteiro=True, mn=1, mx=200)
        self.Lx = campo(0.03843, 5, 0, 10)
        self.Ly = campo(0.03843, 5, 0, 10)
        self.Freq = campo(3.9, 4, 0, 1000)
        self.H = campo(1.524, 4, 0, 1000)
        self.Az_i = campo(0, 2, -90, 90); self.El_i = campo(0, 2, -90, 90)
        self.Az_r = campo(30, 2, -90, 90); self.El_r = campo(0, 2, -90, 90)
        linhas = [("M:", self.M), ("N:", self.N), ("Lx (in meters) :", self.Lx), ("Ly (in meters) :", self.Ly),
                  ("Frequency (GHz) :", self.Freq), ("h (in mm) :", self.H)]
        for i, (t, w) in enumerate(linhas):
            l = QLabel(t); l.setFont(QFont()); l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            G.addWidget(l, i // 2, (i % 2) * 2)
            G.addWidget(w, i // 2, (i % 2) * 2 + 1)
        for col, (titulo, az, el) in enumerate([("Incident Wave :", self.Az_i, self.El_i),
                                                 ("Reflected Wave :", self.Az_r, self.El_r)]):
            p = QGroupBox(titulo)
            g2 = QGridLayout(p)
            g2.addWidget(QLabel("(In degrees)"), 0, 0, 1, 2)
            g2.addWidget(QLabel("Az:"), 1, 0); g2.addWidget(az, 1, 1)
            g2.addWidget(QLabel("El:"), 2, 0); g2.addWidget(el, 2, 1)
            for x in p.findChildren(QLabel):
                x.setFont(QFont())
            G.addWidget(p, 0, 4 + col, 3, 1)
        self.cbCST = QCheckBox(" Run CST Simulation"); self.cbCST.setFont(QFont())
        self.cbCST.setToolTip("Com o Calculate: simula no CST a RIS com o dielétrico real (εr por célula, "
                              f"{ris_cst.nome_solver_diel()}) para o caso escolhido e compara com a teoria")
        self.ddDiel = QComboBox(); self.ddDiel.addItems([rc.nome_caso(nb) for nb in rc.NB_LIST])
        self.ddDiel.setCurrentText(rc.nome_caso(0))
        self.ddDiel.setFont(QFont()); self.ddDiel.setToolTip("Caso simulado com o dielétrico real")
        self.btCalc = QPushButton("Calculate")
        self.btCalc.clicked.connect(lambda: self.calcular(None))
        G.addWidget(barra(self.cbCST, self.ddDiel, stretch_last=False), 3, 4)
        G.addWidget(self.btCalc, 3, 5)
        G.setColumnStretch(1, 1); G.setColumnStretch(3, 1)     # espaço extra vai para as caixas numéricas
        V.addWidget(gb)

        # ---- tabs
        self.tabs = QTabWidget()
        V.addWidget(self.tabs, stretch=1)
        self.tFase, self.figFase = figura()
        self.tCurva, self.figCurva = figura()
        self.tabs.addTab(self.tFase, "Phase Distribution (in degrees)")
        self.tabs.addTab(self.tCurva, "Phase vs Capacitance ")
        self.cont = {}
        for nome in ["RIS Info", "Diagrama de radiação", "Mapa 2D"]:
            w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(6, 6, 6, 0)
            self.cont[nome] = l
            self.tabs.addTab(w, nome)
        self.tabs.setTabVisible(3, False); self.tabs.setTabVisible(4, False)

        # ---- resumo
        self.gbRes = QGroupBox("Resumo")
        f = self.gbRes.font(); f.setBold(True); self.gbRes.setFont(f)
        self.layRes = QHBoxLayout(self.gbRes)
        self.gbRes.setFixedHeight(200)
        V.addWidget(self.gbRes)

    # ------------------------------------------------------------------ fechar a janela
    def closeEvent(self, ev):
        """Com o CST a trabalhar, fechar a app perde os casos que faltam (a lista vive na app) e o campo
        distante do caso atual não é lido. Pede confirmação."""
        if self.worker is not None and self.worker.isRunning():
            r = QMessageBox.warning(self, "CST ainda a trabalhar",
                "O CST ainda está a simular. Se fechares a app agora:\n"
                "  • o caso atual continua no CST, mas os resultados não são lidos para a app;\n"
                "  • os casos seguintes NÃO são simulados.\n\n"
                "(Depois podes voltar a correr o mesmo botão: os casos já feitos são reaproveitados.)\n\n"
                "Fechar mesmo assim?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                ev.ignore()
                return
        ev.accept()

    # ------------------------------------------------------------------ parâmetros
    def params(self):
        return rc.Params(M=self.M.value(), N=self.N.value(), Px=self.Lx.value(), Py=self.Ly.value(),
                         Freq=self.Freq.value(), h_mm=self.H.value(), Az_i=self.Az_i.value(),
                         El_i=self.El_i.value(), Az_r=self.Az_r.value(), El_r=self.El_r.value(),
                         usarDelta=self.usarDelta)

    # ------------------------------------------------------------------ cálculo (CalculateButtonPushed)
    def calcular(self, fonte=None):
        """fonte: None (Calculate) ou 'btDelta'."""
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "CST", "O CST ainda está a trabalhar; espera que termine.")
            return
        self._guardar_selecoes()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            R = rc.calcular(self.params())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro no cálculo", str(e))
            return
        QApplication.restoreOverrideCursor()
        self.R = R
        for l in R.log:
            print(l)
        print(f"εr por célula ({R.res[rc.CASO_CST].nome}):")
        print(np.round(R.res[rc.CASO_CST].er, 2))
        if R.curva.aviso:
            QMessageBox.warning(self, "Curva para este ângulo em falta", R.curva.aviso)

        # Resultados CST (dielétrico real): só valem enquanto os parâmetros forem os mesmos
        self.assin = rc.assinatura(R.p, R.curva.ficheiro)
        if self.memDiel["assinatura"] != self.assin:
            self.memDiel = {"assinatura": self.assin, "val": {}}
        self.memDiel["val"].update(rc.ler_mats(self.assin, R.nomes, prefixo=rc.PREF_DIEL))

        self.renderizar()

        # Resultados CST gravados antes da RCS (|E| em V/m): voltar a ler os projetos (sem simular)
        antigos = {c: f for c, f in rc.mats_antigos(self.assin, R.nomes).items() if c not in self.memDiel["val"]}
        if fonte is None and antigos:
            r = QMessageBox.question(self, "Resultados CST em |E|",
                "Estes resultados CST foram gravados como |E| em dB(V/m), antes de a app usar a RCS:\n  "
                + "\n  ".join(f"{c}: {os.path.basename(f)}" for c, f in antigos.items())
                + "\n\nLer de novo esses projetos no CST, agora em RCS [dB(mm²)]? "
                  "(não volta a simular; os .mat antigos ficam guardados como *_Efield.mat)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r == QMessageBox.Yes:
                if self.cbCST.isChecked():
                    print('» "Run CST Simulation" fica para o próximo Calculate (primeiro a releitura em RCS).')
                self._correr_cst([(c, f, False) for c, f in antigos.items()], selecionar=False, modelo="diel")
                print("» Cálculo concluído.")
                return

        if fonte is None and self.cbCST.isChecked():
            self._validar_diel(self.ddDiel.currentText())
        print("» Cálculo concluído.")

    def _validar_diel(self, caso):
        """'Run CST Simulation': RIS com o dielétrico real no CST para o caso escolhido. Se já existir uma
        simulação com as mesmas características, pergunta se a usa (por defeito) ou se simula de novo."""
        R = self.R
        q = R.nomes.index(caso)
        if caso in self.memDiel["val"]:
            existente, jaCarregado = self.memDiel["val"][caso].ficheiro, True
        else:
            existente, jaCarregado = rc.procurar_diel(R, q), False   # só projetos que chegaram ao fim
        simular = True
        if existente:
            r = QMessageBox.question(self, "Run CST Simulation",
                f'Já existe uma simulação do dielétrico real para "{caso}" com as mesmas características:\n'
                f'{os.path.basename(existente)}\n\nUsar essa (Sim) ou simular de novo (Não)?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            simular = (r == QMessageBox.No)
        if not simular and jaCarregado:
            self._guardar_selecoes(); self.selDiag = caso
            self.tabs.setCurrentIndex(3); self.renderizar()
            return
        if simular:
            r = QMessageBox.question(self, "Run CST Simulation",
                f'Vou simular no CST a RIS com o dielétrico real ("{caso}", {int(R.p.M)}×{int(R.p.N)} células, '
                f'εr até {np.nanmax(R.res[q].er):.0f}) com o {ris_cst.nome_solver_diel()}'
                f'{" e simetria em y (metade do modelo)" if ris_cst._vba_simetria(R) else ""}.\n\n'
                'Pode demorar bastante (εr alto -> muitas células). Continuar?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r != QMessageBox.Yes:
                return
        self._correr_cst([(caso, "" if simular else existente, simular)], selecionar=True, modelo="diel")

    def _correr_cst(self, jobs, selecionar, modelo="diel"):
        dlg = QProgressDialog("A ligar ao CST...", None, 0, 0, self)
        dlg.setWindowTitle("CST" if modelo == "ibc" else "Run CST Simulation (dielétrico real)")
        dlg.setWindowModality(Qt.WindowModal); dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(560)
        dlg.show()
        self.worker = TrabalhoCST(self.R, jobs, self.assin, modelo)
        mem = self.memCST if modelo == "ibc" else self.memDiel

        # ---- relógio e estimativa do tempo que falta (atualizado 1x/s; não interfere com o CST)
        # Estimativa por caso: média das durações já feitas nesta corrida; antes disso, média das
        # simulações anteriores com o mesmo M x N (campo 'tempo' dos .mat). Leituras de projetos ~ rápidas.
        prefixo = rc.PREF_IBC if modelo == "ibc" else rc.PREF_DIEL
        hist = rc.tempos_historicos(self.R.p.M, self.R.p.N, prefixo)
        est = {"msg": "A ligar ao CST...", "t0": time.time(), "tCaso": time.time(), "k": 0,
               "sim": [], "ler": []}

        def media(lst, reserva):
            return sum(lst) / len(lst) if lst else reserva

        def mmss(s):
            s = int(round(s)); h, r = divmod(s, 3600); m, s = divmod(r, 60)
            return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

        def atualizar():
            agora = time.time()
            dC, dT = agora - est["tCaso"], agora - est["t0"]
            eSim = media(est["sim"], media(hist, None))
            eLer = media(est["ler"], 30.0)
            linhas = [est["msg"], "",
                      f"Decorrido:  {mmss(dC)} neste caso   ·   {mmss(dT)} no total"]
            k = est["k"]
            if k < len(jobs):
                restantes = jobs[k + 1:]
                atual = eSim if jobs[k][2] else eLer
                if atual is None or (any(j[2] for j in restantes) and eSim is None):
                    linhas.append("Tempo restante: sem estimativa até terminar o 1.º caso simulado")
                else:
                    falta = max(atual - dC, 0) + sum(eSim if j[2] else eLer for j in restantes)
                    base = "desta corrida" if est["sim"] else f"{len(hist)} simulação(ões) anteriores"
                    txt = f"Tempo restante estimado:  ~{mmss(falta)}   (média por caso: {mmss(atual)}, {base})"
                    if dC > atual:
                        txt += "\nEste caso já demora mais do que a média…"
                    linhas.append(txt)
            dlg.setLabelText("\n".join(linhas))

        def novo_msg(t):
            est["msg"] = t; atualizar()

        def inicio(k):
            est["k"] = k; est["tCaso"] = time.time(); atualizar()

        def fim_caso(dur, simulou):
            est["sim" if simulou else "ler"].append(dur)

        self.worker.progresso.connect(novo_msg)
        self.worker.inicioCaso.connect(inicio)
        self.worker.fimCaso.connect(fim_caso)
        relogio = QTimer(dlg)
        relogio.timeout.connect(atualizar)
        relogio.start(1000)
        atualizar()

        def fim(ok, falhas):
            relogio.stop()
            dlg.close()
            for v in ok:
                mem["val"][v.caso] = v
            if ok and modelo == "diel":
                self.tabs.setCurrentIndex(3)
            self._guardar_selecoes()
            if selecionar and ok:
                self.selDiag = ok[0].caso
            self.renderizar()
            if falhas:
                QMessageBox.warning(self, "CST", "Alguns casos falharam no CST:\n" + "\n".join(falhas))
        self.worker.feito.connect(fim)
        self.worker.start()

    # ------------------------------------------------------------------ manter tabs/casos abertos
    def _guardar_selecoes(self):
        def sel(attr):
            t = getattr(self, attr, None)
            try:
                return t.tabText(t.currentIndex()) if t is not None else None
            except RuntimeError:
                return None
        self.selInfo = sel("tgInfo") or self.selInfo
        self.selDiag = sel("tgDiag") or self.selDiag
        self.selMapa = sel("tgMapa") or self.selMapa

    @staticmethod
    def _repor(tg, nome):
        for i in range(tg.count()):
            if tg.tabText(i) == nome:
                tg.setCurrentIndex(i)

    # ------------------------------------------------------------------ desenho de todas as tabs
    def renderizar(self):
        R = self.R
        atual = self.tabs.currentIndex()
        self._render_fase(R)
        self._render_curva(R)
        self._render_info(R)
        self._render_diagrama(R)
        self._render_mapa(R)
        self._render_resumo(R)
        self.tabs.setTabText(1, "Fase vs εr")
        self.tabs.setTabVisible(3, True); self.tabs.setTabVisible(4, True)
        self.tabs.setCurrentIndex(atual)

    def _render_fase(self, R):
        p = R.p; M, N = int(p.M), int(p.N)
        fig = self.figFase; fig.clear()
        ax = fig.add_subplot()
        im = ax.imshow(R.PHASE_deg, cmap="viridis", vmin=0, vmax=360, aspect="auto",
                       extent=[0.5, N + 0.5, M + 0.5, 0.5], interpolation="nearest")
        fig.colorbar(im, ax=ax).set_label("Fase [°]", fontsize=14)
        passo = max(1, int(np.ceil(max(M, N) / 20)))
        ax.set_xticks(range(1, N + 1, passo)); ax.set_yticks(range(1, M + 1, passo))
        ax.tick_params(labelsize=12)
        ax.set_xlabel("Índice da célula (x)", fontsize=14); ax.set_ylabel("Índice da célula (y)", fontsize=14)
        ax.set_title("Distribuição de fase ideal\n"
                     f"Az = {rc.g(p.Az_r)}°, El = {rc.g(p.El_r)}°  (referência de fase: célula de fase mínima = 0°)",
                     fontsize=13)
        fig.canvas.draw_idle()

    def _render_curva(self, R):
        cv = R.curva
        fig = self.figCurva; fig.clear()
        ax = fig.add_subplot()
        ax.plot(cv.er, cv.fase, "-+", lw=2, ms=8)
        ax.set_title(f"Fase refletida × εr   ({cv.ficheiro})", fontsize=14)
        ax.set_xlabel("$\\epsilon_r$", fontsize=14); ax.set_ylabel("Fase refletida relativa [°]", fontsize=14)
        ax.grid(True, ls="--", alpha=0.25)
        fig.canvas.draw_idle()

    # ---- RIS Info
    def _render_info(self, R):
        w = QWidget(); V = QVBoxLayout(w); V.setContentsMargins(0, 0, 0, 0)
        bt = QPushButton()
        bt.setCheckable(True); bt.setChecked(self.usarDelta)
        f = bt.font(); f.setBold(True); bt.setFont(f)
        if self.usarDelta:
            bt.setText("Constante de fase δ: LIGADA"); bt.setStyleSheet("background-color: rgb(204,235,204);")
        else:
            bt.setText("Constante de fase δ: DESLIGADA (δ = 0°)"); bt.setStyleSheet("background-color: rgb(245,217,217);")
        bt.setFixedWidth(300)

        def alternar(v):
            self.usarDelta = v
            self.calcular("btDelta")
        bt.toggled.connect(alternar)
        sw, gr = seletor(["Fase ideal", "Fase realizável"], 0 if self.mostrarIdeal else 1)
        V.addWidget(barra(bt, negrito("Mostrar:"), sw,
                          italico("δ: ligada = δ ótimo em cada caso; desligada = δ = 0°.   O εr vem sempre da fase realizável.")))

        tg = QTabWidget()
        pilhas = []
        for r in R.res:
            tq = QWidget(); g = QVBoxLayout(tq)
            txtDelta = f"(constante de fase δ = {r.desvio:.1f}°)" if self.usarDelta else "(sem constante de fase, δ = 0°)"
            tituloReal = (f"Fase realizável — contínuo [°]   {txtDelta}" if r.nbits == 0
                          else f"Fase realizável — quantizada a {r.nbits} bit(s) [°]   {txtDelta}")
            st = QStackedWidget()
            for titulo, M_ in [(f"Fase ideal [°]   {txtDelta}", r.faseIdeal), (tituloReal, r.fase)]:
                pw = QWidget(); pl = QVBoxLayout(pw); pl.setContentsMargins(0, 0, 0, 0)
                pl.addWidget(negrito(titulo)); pl.addWidget(tabela(np.round(M_, 1).tolist(), 1))
                st.addWidget(pw)
            st.setCurrentIndex(0 if self.mostrarIdeal else 1)
            pilhas.append(st)
            g.addWidget(st, stretch=1)
            g.addWidget(negrito(f"εr por célula  ({r.nEr} valores diferentes, {r.nAjust} células ajustadas ao "
                                f"extremo da curva)   valores: {r.valores}"))
            g.addWidget(tabela(np.round(r.er, 2).tolist(), 2), stretch=1)
            tg.addTab(tq, r.nome)

        def trocar(i):
            self.mostrarIdeal = (i == 0)
            for s in pilhas:
                s.setCurrentIndex(i)
        gr.idClicked.connect(trocar)
        self._repor(tg, self.selInfo)
        self.tgInfo = tg
        V.addWidget(tg, stretch=1)
        substituir(self.cont["RIS Info"], w)

    # ---- Normalização (seletor nas tabs Diagrama e Mapa 2D)
    def _seletor_norm(self):
        """Botão ON/OFF 'Normalizado' (ON: 0 dB = pico do contínuo; OFF: valores sem normalizar)."""
        bt = QPushButton()
        bt.setCheckable(True)
        bt.setChecked(self.normalizar)
        bt.setFixedWidth(52)
        bt.setToolTip("ON: 0 dB = pico do contínuo.\nOFF: RCS em dB(mm²), na teoria (ótica física: "
                      "4π·(a/λ)²·(|fator de rede|·cosθ)², a = Lx·Ly) e no CST (modo RCS do campo distante), "
                      "no mesmo eixo. As escalas ficam ancoradas no máximo teórico do contínuo.")

        def aspeto(on):
            bt.setText("ON" if on else "OFF")
            bt.setStyleSheet("QPushButton { font-weight: bold; color: white; border-radius: 4px; padding: 3px; "
                             f"background-color: {'#2e7d32' if on else '#9e9e9e'}; }}")
        aspeto(self.normalizar)

        def trocar(on):
            aspeto(on)
            if self.normalizar == on:
                return
            self.normalizar = on
            self._guardar_selecoes()
            atual = self.tabs.currentIndex()
            self._render_diagrama(self.R)
            self._render_mapa(self.R)
            self.tabs.setCurrentIndex(atual)
        bt.toggled.connect(trocar)
        return bt

    def _fontes_cst(self):
        """(nome, memória, estilo, cor) de cada fonte CST mostrada (só o dielétrico real; o IBC foi retirado
        da interface — o código IBC continua em ris_cst.validar)."""
        return [("CST (dielétrico)", self.memDiel, "-.", (0.10, 0.55, 0.20))]

    def _ref_cst(self, campo, val=None):
        """Referência do CST (valores sem normalizar): contínuo se carregado, senão o maior dos casos carregados."""
        cont = self.R.nomes[rc.I_CONT]
        fontes = [val] if val is not None else [self.memDiel["val"]]
        for vf in fontes:
            if cont in vf:
                return float(getattr(vf[cont], campo).max())
        todos = [float(getattr(v, campo).max()) for vf in fontes for v in vf.values()]
        return max(todos) if todos else 1.0

    # ---- Diagrama de radiação
    def _cst_curva(self, v, Az_r):
        aC = v.ang
        yC = 20 * np.log10(v.campo / v.campo.max() + rc.EPS)
        ip, iL, iR, isl = rc.lobos_de(yC, aC, Az_r)
        return aC, yC, ip, float(yC[isl] - yC[ip])

    def _render_diagrama(self, R):
        p = R.p
        w = QWidget(); V = QVBoxLayout(w); V.setContentsMargins(0, 0, 0, 0)
        nomesDiel = [n for n in R.nomes if n in self.memDiel["val"]]
        if not nomesDiel:
            txt = 'Sem resultados CST: "Run CST Simulation" (dielétrico real) com o caso escolhido + Calculate.'
        else:
            txt = f"CST dielétrico (traço-ponto verde): {', '.join(nomesDiel)}."
        V.addWidget(barra(negrito("Normalizado:"), self._seletor_norm(), italico(txt)))

        # Normalizado: 0 dB = pico do contínuo.  Não normalizado: RCS em dB(mm²) — teoria (off = RCS do pico
        # do contínuo) e CST no mesmo eixo; o eixo fica ancorado no contínuo teórico.
        norm = self.normalizar
        off = 0.0 if norm else 20 * np.log10(R.refC) + R.cRCS
        unT = "dB" if norm else "dB(mm²)"
        ylab = "Nível normalizado [dB]" if norm else "RCS [dB(mm²)]"

        tg = QTabWidget()
        # todos os casos (só teoria)
        tw, fig = figura(); ax = fig.add_subplot()
        for q, r in enumerate(R.res):
            L = R.lob[q]
            ax.plot(R.angF, rc.dB(R.CUT[q], R.refC) + off, ESTILOS[r.nbits], color=CORES[r.nbits], lw=1.6,
                    label=f"{r.nome} (pico {L.pico + off:.1f} {unT}, SLL {L.sll:.1f} dB)")
        xline(ax, p.Az_r, "--", f"Objetivo {rc.g(p.Az_r)}°", VERDE_OBJ)
        if R.espNoCorte:
            xline(ax, R.az_s, ":", f"Especular {rc.g(R.az_s)}°", LARANJA_ESP)
        self._eixos_corte(ax, f"Corte em azimute (El = {rc.g(p.El_r)}°), "
                              + ("normalizado" if norm else "não normalizado"),
                          off, ylab)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=9)
        tg.addTab(tw, "Todos os casos")

        for q, r in enumerate(R.res):
            y = rc.dB(R.CUT[q], R.refC) + off; L = R.lob[q]
            pk = L.pico + off
            tw, fig = figura(); ax = fig.add_subplot()
            xs = R.angF[L.iL:L.iR + 1]
            ax.fill_between(xs, y[L.iL:L.iR + 1], -40 + off, color=(0.30, 0.60, 0.90), alpha=0.25, lw=0,
                            label="Lobo principal")
            ax.plot(R.angF, y, "-", color=CORES[r.nbits], lw=1.8, label="Diagrama (teoria)")
            ax.plot(L.angPico, pk, "o", ms=8, mfc=(0.30, 0.60, 0.90), mec="k",
                    label=f"Pico: {pk:.1f} {unT} a {L.angPico:.1f}°")
            ax.plot(L.angSll, pk + L.sll, "v", ms=8, mfc=(0.90, 0.30, 0.20), mec="k",
                    label=f"Maior lobo secundário: {pk + L.sll:.1f} {unT} a {L.angSll:.1f}°")
            ax.axhline(pk + L.sll, ls="--", color=(0.90, 0.30, 0.20), lw=1.2)
            ax.text(-89, pk + L.sll + 0.5, f"SLL = {L.sll:.1f} dB", color=(0.90, 0.30, 0.20), fontsize=8)
            xline(ax, p.Az_r, "--", f"Objetivo {rc.g(p.Az_r)}°", VERDE_OBJ)
            if R.espNoCorte:
                xline(ax, R.az_s, ":", f"Especular {rc.g(R.az_s)}°", LARANJA_ESP)
            txtCST = ""
            hs, ls_ = ax.get_legend_handles_labels()
            for nomeF, memF, estF, corF in self._fontes_cst():
                if r.nome not in memF["val"]:
                    continue
                v = memF["val"][r.nome]
                aC, yC, ipC, sllC = self._cst_curva(v, p.Az_r)
                curto = nomeF.replace("CST (", "").rstrip(")")
                if norm:
                    # CST normalizado ao próprio pico e alinhado com o pico teórico
                    h, = ax.plot(aC, np.maximum(yC + L.pico, -40), estF, color=corF, lw=1.6)
                    txtCST += f"   |   {curto}: pico a {aC[ipC]:.1f}° · SLL {sllC:.1f} dB"
                    lab = f"{nomeF}: pico a {aC[ipC]:.1f}°, SLL {sllC:.1f} dB"
                else:
                    # CST sem normalizar: RCS em dB(mm²), no mesmo eixo da teoria
                    yO = 20 * np.log10(v.campo + rc.EPS)
                    h, = ax.plot(aC, np.maximum(yO, off - 40), estF, color=corF, lw=1.6)
                    txtCST += f"   |   {curto}: {yO[ipC]:.1f} dB(mm²) a {aC[ipC]:.1f}°"
                    lab = (f"{nomeF}: pico {yO[ipC]:.1f} dB(mm²) a {aC[ipC]:.1f}° "
                           f"({yO[ipC] - pk:+.1f} dB face à teoria), SLL {sllC:.1f} dB")
                hs.append(h); ls_.append(lab)
            self._eixos_corte(ax, f"{r.nome} — lobo principal {pk:.1f} {unT} a {L.angPico:.1f}° · "
                                  f"SLL {L.sll:.1f} dB{txtCST}", off, ylab)
            ax.legend(hs, ls_, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=1, fontsize=9)
            tg.addTab(tw, r.nome)
        self._repor(tg, self.selDiag)
        self.tgDiag = tg
        V.addWidget(tg, stretch=1)
        substituir(self.cont["Diagrama de radiação"], w)

    @staticmethod
    def _eixos_corte(ax, titulo, off=0.0, ylab="Nível normalizado [dB]"):
        ax.set_xlim(-90, 90); ax.set_ylim(-40 + off, 3 + off); ax.set_xticks(range(-90, 91, 15))
        ax.set_xlabel("Azimute [°]"); ax.set_ylabel(ylab)
        ax.grid(True, alpha=0.3); ax.set_title(titulo, fontsize=10)

    # ---- Mapa 2D
    def _marcas_mapa(self, ax, AZ, EL, Z, R, espelho=False):
        p = R.p
        cand = np.flatnonzero(Z.ravel() >= Z.max() - 0.1)
        ipk = cand[np.argmin((AZ.ravel()[cand] - p.Az_r) ** 2 + (EL.ravel()[cand] - p.El_r) ** 2)]
        az_p, el_p = AZ.ravel()[ipk], EL.ravel()[ipk]
        if espelho:
            um = -2 * R.ui - R.ur; vm = -2 * R.vi - R.vr
            if abs(vm) <= 1:
                el_m = rc.asind(vm)
                if abs(um / rc.cosd(el_m)) <= 1:
                    az_m = rc.asind(um / rc.cosd(el_m))
                    ax.plot(az_m, el_m, "w^", ms=10, mew=2, mfc="none")
                    ax.text(az_m + 3, el_m - 6, "Lobo espelho", color="w")
        ax.plot(az_p, el_p, "wo", ms=10, mew=2, mfc="none")
        ax.text(az_p + 3, el_p + 6, f"Pico ({az_p:.0f}°, {el_p:.0f}°)", color="w", fontweight="bold")
        ax.plot(p.Az_r, p.El_r, "w+", ms=14, mew=2)
        ax.text(p.Az_r + 3, p.El_r - 6, "Objetivo", color="w")
        ax.plot(R.az_s, R.el_s, "wx", ms=12, mew=2)
        ax.text(R.az_s + 3, R.el_s - 6, "Especular", color="w")
        ax.set_xlim(-90, 90); ax.set_ylim(-90, 90)
        ax.set_xticks(range(-90, 91, 30)); ax.set_yticks(range(-90, 91, 30))
        ax.set_xlabel("Azimute [°]"); ax.set_ylabel("Elevação [°]")
        return ipk, az_p, el_p

    @staticmethod
    def _imagem(fig, ax, x, y, Z, off=0.0, rotulo="Nível normalizado [dB]"):
        dx = (x[1] - x[0]) / 2 if len(x) > 1 else 0.5
        dy = (y[1] - y[0]) / 2 if len(y) > 1 else 0.5
        im = ax.imshow(Z, origin="lower", cmap="turbo", vmin=-40 + off, vmax=off, aspect="auto",
                       interpolation="nearest", extent=[x[0] - dx, x[-1] + dx, y[0] - dy, y[-1] + dy])
        fig.colorbar(im, ax=ax).set_label(rotulo)
        return im

    def _render_mapa(self, R):
        p = R.p
        fontes = self._fontes_cst()                          # [(nome, memória, estilo, cor)]: dielétrico
        w = QWidget(); V = QVBoxLayout(w); V.setContentsMargins(0, 0, 0, 0)
        sw, gr = seletor(["App (teoria)"] + [f[0] for f in fontes], self.fonteMapa)
        disp = [f"{nomeF}: {', '.join(n for n in R.nomes if n in memF['val'])}"
                for nomeF, memF, _, _ in fontes if any(n in memF["val"] for n in R.nomes)]
        txtM = ("Disponível — " + "; ".join(disp) + "." if disp else
                'Sem resultados CST: "Run CST Simulation" (dielétrico real) com o caso escolhido + Calculate.')
        V.addWidget(barra(negrito("Mostrar:"), sw, negrito("Normalizado:"), self._seletor_norm(), italico(txtM)))

        AZ, EL = np.meshgrid(R.azg, R.elg)
        nomeCont = R.nomes[rc.I_CONT]
        norm = self.normalizar
        # Não normalizado: RCS em dB(mm²) na teoria e no CST, com a mesma escala de cores
        # (do máximo teórico do contínuo até 40 dB abaixo)
        offT = 0.0 if norm else 20 * np.log10(R.ref2) + R.cRCS
        rotT = "Nível normalizado [dB]" if norm else "RCS [dB(mm²)]"
        rotC = rotT
        unT = "dB" if norm else "dB(mm²)"

        def txt_norm(val):
            if not norm:
                return "não normalizado"
            return "normalizado" if nomeCont in val else "normalizado ao próprio pico"

        tg = QTabWidget()
        pilhas = []
        for q, r in enumerate(R.res):
            st = QStackedWidget()
            # App (teoria)
            tw, fig = figura(); ax = fig.add_subplot()
            Z = rc.dB(R.MAP[q], R.ref2) + offT
            self._imagem(fig, ax, R.azg, R.elg, Z, offT, rotT)
            self._marcas_mapa(ax, AZ, EL, Z, R, espelho=(r.nbits == 1))
            L = R.lob[q]
            ax.set_title(f"App — {r.nome}: lobo principal {L.pico + offT:.1f} {unT} a {L.angPico:.1f}° · "
                         f"SLL {L.sll:.1f} dB", fontsize=10)
            st.addWidget(tw)
            # CST (dielétrico)
            for nomeF, memF, _, _ in fontes:
                val = memF["val"]
                if r.nome not in val:
                    lb = italico(f'Sem resultados {nomeF} para "{r.nome}" com estes parâmetros.\n'
                                 'Usa "Run CST Simulation" com este caso escolhido + Calculate.')
                    lb.setAlignment(Qt.AlignCenter)
                    st.addWidget(lb)
                    continue
                v = val[r.nome]
                if norm:
                    ref = float(val[nomeCont].mapCampo.max()) if nomeCont in val else float(v.mapCampo.max())
                    ZC = np.maximum(20 * np.log10(v.mapCampo / ref + rc.EPS), -40); oC = 0.0; un = "dB"
                else:
                    ZC = np.maximum(20 * np.log10(v.mapCampo + rc.EPS), offT - 40); oC = offT; un = "dB(mm²)"
                tw, fig = figura(); ax = fig.add_subplot()
                self._imagem(fig, ax, v.mapAz, v.mapEl, ZC, oC, rotC)
                AZm, ELm = np.meshgrid(v.mapAz, v.mapEl)
                ipC, a_, e_ = self._marcas_mapa(ax, AZm, ELm, ZC, R)
                txtLeg = " · lido de projeto existente" if v.legado else ""
                ax.set_title(f"{nomeF} — {r.nome}: pico {ZC.ravel()[ipC]:.1f} {un} em ({a_:.0f}°, {e_:.0f}°) · "
                             f"{txt_norm(val)}{txtLeg}", fontsize=10)
                st.addWidget(tw)
            st.setCurrentIndex(self.fonteMapa)
            pilhas.append(st)
            tg.addTab(st, r.nome)

        # Casos x Azimute (corte em El = El_r): teoria e CST (dielétrico)
        iel = int(np.argmin(np.abs(R.elg - p.El_r)))
        nR = len(R.res)
        refRow = R.MAP[rc.I_CONT][iel, :].max()
        offR = 0.0 if norm else 20 * np.log10(refRow) + R.cRCS
        txtApp = "normalizado" if norm else "não normalizado"
        paineis = [(np.array([rc.dB(R.MAP[q][iel, :], refRow) + offR for q in range(nR)]),
                    f"App — nível por caso (corte El = {rc.g(p.El_r)}°), {txtApp}", offR, rotT)]
        for nomeF, memF, _, _ in fontes:
            val = memF["val"]
            ZZ = np.full((nR, len(R.azg)), np.nan)
            for q, r in enumerate(R.res):
                if r.nome in val:
                    v = val[r.nome]
                    if norm:
                        rC = float(val[nomeCont].campo.max()) if nomeCont in val else v.campo.max()
                        ZZ[q] = np.interp(R.azg, v.ang, np.maximum(20 * np.log10(v.campo / rC + rc.EPS), -40))
                    else:
                        ZZ[q] = np.interp(R.azg, v.ang, np.maximum(20 * np.log10(v.campo + rc.EPS), offR - 40))
            paineis.append((ZZ, f"{nomeF} — nível por caso (corte El = {rc.g(p.El_r)}°), {txt_norm(val)} "
                                "(linhas vazias = sem resultados)", offR, rotC))
        st = QStackedWidget()
        for Zx, ttl, oX, rot in paineis:
            tw, fig = figura(); ax = fig.add_subplot()
            self._imagem(fig, ax, R.azg, np.arange(1, nR + 1), Zx, oX, rot)
            ax.axvline(p.Az_r, ls="--", color="w", lw=1.5)
            ax.text(p.Az_r, nR + 0.4, " Objetivo", color="w", va="top")
            if R.espNoCorte:
                ax.axvline(R.az_s, ls=":", color="w", lw=1.5)
                ax.text(R.az_s, nR + 0.4, " Especular", color="w", va="top")
            for q in range(nR):
                if np.all(np.isnan(Zx[q])):
                    continue
                ip = int(np.nanargmax(Zx[q]))
                ax.plot(R.azg[ip], q + 1, "wo", ms=8, mew=2, mfc="none")
            ax.set_yticks(range(1, nR + 1)); ax.set_yticklabels(R.nomes)
            ax.set_xlim(-90, 90); ax.set_xticks(range(-90, 91, 15)); ax.set_ylim(0.5, nR + 0.5)
            ax.set_xlabel("Azimute [°]"); ax.set_title(ttl, fontsize=10)
            st.addWidget(tw)
        st.setCurrentIndex(self.fonteMapa)
        pilhas.append(st)
        tg.addTab(st, "Casos × Azimute")

        def trocar(i):
            self.fonteMapa = i
            for s in pilhas:
                s.setCurrentIndex(i)
        gr.idClicked.connect(trocar)
        self._repor(tg, self.selMapa)
        self.tgMapa = tg
        V.addWidget(tg, stretch=1)
        substituir(self.cont["Mapa 2D"], w)

    # ---- Resumo
    def _render_resumo(self, R):
        p, cv = R.p, R.curva
        w = QWidget(); H = QHBoxLayout(w); H.setContentsMargins(0, 0, 0, 0)
        txt = "\n".join([
            f"Frequência: {p.Freq:.3g} GHz  (λ = {R.lam*1e3:.1f} mm)",
            f"Array: {int(p.M)} × {int(p.N)} células de {p.Px*1e3:.1f} × {p.Py*1e3:.1f} mm",
            f"Espessura do dielétrico: h = {rc.g(p.h_mm)} mm",
            f"Incidência: Az {rc.g(p.Az_i)}°, El {rc.g(p.El_i)}°   |   Reflexão: Az {rc.g(p.Az_r)}°, El {rc.g(p.El_r)}°",
            f"Passo de fase por célula: x = {R.passoX:.2f}°, y = {R.passoY:.2f}°",
            f"Curva: {cv.ficheiro} (εr {cv.er.min():.0f}–{cv.er.max():.0f}, cobre 0–{cv.phMax:.1f}°)"])
        lb = QLabel(txt); lb.setFont(QFont()); lb.setAlignment(Qt.AlignTop); lb.setFixedWidth(340)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        H.addWidget(lb)
        linhas = []
        for r, L in zip(R.res, R.lob):
            est = "contínuo" if r.nbits == 0 else str(2 ** r.nbits)
            linhas.append([r.nome, est, rc.g(r.desvio), f"{r.perda:.2f}", f"{r.erroMax:.1f}", r.nEr, r.nAjust,
                           f"{L.pico:.2f}", f"{L.angPico:.2f}", f"{L.sll:.1f}"])
        t = tabela(linhas, cabecalho=["Caso", "Estados", "δ (°)", "Perda fase (dB)", "Erro máx (°)", "Nº εr",
                                      "Células ajust.", "Pico (dB)", "Pico (°)", "SLL (dB)"])
        t.setFont(QFont())
        H.addWidget(t, stretch=1)
        substituir(self.layRes, w)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    app = QApplication(sys.argv)
    w = App()
    w.show()
    if "--calc" in sys.argv:
        w.calcular(None)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
