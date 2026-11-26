"""
Ligação ao CST Studio Suite por COM (pywin32) — os mesmos comandos VBA da app MATLAB:
  * validar(): modelo IBC (uma impedância de superfície por célula), onda plana,
    monitor de campo distante, I-solver (RunSolver) e leitura do campo distante;
  * ler_existente(): abre um projeto já simulado e lê o campo distante;
  * criar_array(): "Run CST Simulation" — cria o array dielétrico completo (só grava).
Chamar sempre de uma thread com COM inicializado (a app faz pythoncom.CoInitialize()).
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import numpy as np

import ris_core as rc


class _COM:
    """Objeto COM do CST em que tudo é chamado como MÉTODO (como o invoke do MATLAB).
    O pywin32 dinâmico, ao ler obj.Nome, tenta primeiro uma propriedade (chamada com 0 argumentos)
    e o CST responde "This method call requires a different number of arguments"."""

    def __init__(self, o):
        self._o = o

    def __getattr__(self, nome):
        self._o._FlagAsMethod(nome)
        f = getattr(self._o, nome)

        def chamar(*args):
            r = f(*args)
            return _COM(r) if hasattr(r, "_FlagAsMethod") else r
        return chamar


def _cst():
    import win32com.client.dynamic
    return _COM(win32com.client.dynamic.Dispatch("CSTStudio.application"))


def _get(obj, nome):
    """invoke(obj, nome) do MATLAB: método sem argumentos."""
    return getattr(obj, nome)()


def _H(mws, nome, vba):
    mws.AddToHistory(nome, vba)


def _ler_farfield(mws, freq, El_r, progresso=None):
    fs = rc.g(freq)
    nomeFF = f"farfield (f={fs}) [pw]"
    mws.SelectTreeItem("Farfields\\" + nomeFF)
    ff = _get(mws, "FarfieldPlot")
    angV, ptsV, azC, elC, ptsM, forma = rc.pontos_farfield(El_r)

    def lista(P, modo):
        ff.Reset()
        ff.SetPlotMode(modo)
        ff.SetScaleLinear("True")
        for th, ph in P:
            ff.AddListEvaluationPoint(float(th), float(ph), 0, "spherical", "frequency", float(freq))
        ff.CalculateList(nomeFF)
        v = np.asarray(ff.GetList("Spherical abs"), float).ravel()
        return v[-len(P):]                               # só os pontos pedidos nesta lista

    # RCS do CST (linear, m²) no corte e no mapa
    rcsV, rcsM = lista(ptsV, "rcs"), lista(ptsM, "rcs")
    # Verificação das unidades com o campo |E| do corte (onda plana de 1 V/m, campo distante a 1 m):
    # σ = 4π·|E|². Se o CST devolver noutra unidade (ex.: mm²), a razão é uma potência de 10⁶ e corrige-se.
    E = lista(ptsV, "Efield")
    i = int(np.argmax(E))
    if E[i] > 0 and rcsV[i] > 0:
        esc = 10.0 ** (6 * round(np.log10(rcsV[i] / (4 * np.pi * E[i] ** 2)) / 6))
        rcsV, rcsM = rcsV / esc, rcsM / esc
    amp = lambda s: np.sqrt(np.maximum(s, 0) * 1e6)      # m² -> √(mm²): 20·log10 = RCS em dB(mm²)
    return angV, amp(rcsV), azC, elC, amp(rcsM).reshape(forma, order="F")


def _vba_onda_monitor(R: rc.Resultado) -> str:
    """Fronteiras open, onda plana a chegar da direção de incidência (polarização horizontal:
    E no plano x-z) e monitor de campo distante na frequência de trabalho."""
    fs = rc.g(R.p.Freq)
    kP = R.kP
    Ev = np.array([1.0, 0, 0]) - np.dot([1, 0, 0], kP) * kP
    if np.linalg.norm(Ev) < 1e-6:
        Ev = np.array([0, 1.0, 0]) - np.dot([0, 1, 0], kP) * kP
    Ev = Ev / np.linalg.norm(Ev)
    return ('With Boundary\n.Xmin "expanded open"\n.Xmax "expanded open"\n.Ymin "expanded open"\n'
            '.Ymax "expanded open"\n.Zmin "expanded open"\n.Zmax "expanded open"\n'
            '.Xsymmetry "none"\n.Ysymmetry "none"\n.Zsymmetry "none"\nEnd With\n'
            f'With PlaneWave\n.Reset\n.Normal "{kP[0]:.6f}", "{kP[1]:.6f}", "{kP[2]:.6f}"\n'
            f'.EVector "{Ev[0]:.6f}", "{Ev[1]:.6f}", "{Ev[2]:.6f}"\n'
            f'.Polarization "Linear"\n.ReferenceFrequency "{fs}"\n.PhaseDifference "-90.0"\n'
            '.CircularDirection "Left"\n.AxialRatio "0.0"\n.SetUserDecouplingPlane "False"\n.Store\nEnd With\n'
            f'With Monitor\n.Reset\n.Name "farfield (f={fs})"\n.Domain "Frequency"\n.FieldType "Farfield"\n'
            f'.MonitorValue "{fs}"\n.ExportFarfieldSource "False"\n.UseSubvolume "False"\n.Create\nEnd With\n')


def validar(R: rc.Resultado, caso: str, progresso=print) -> rc.ValCST:
    """Cria e simula o caso no CST (onda plana -> campo distante) e devolve o resultado."""
    p = R.p
    M, N = int(p.M), int(p.N)
    q = R.nomes.index(caso)
    t0 = time.time()
    os.makedirs(rc.PASTA_VAL, exist_ok=True)
    fs = rc.g(p.Freq)
    PxMM, PyMM = p.Px * 1e3, p.Py * 1e3

    progresso(f'A criar e a simular o caso "{caso}" no CST (onda plana + campo distante)...')
    Zr = np.round(R.ZsCasos[q].ravel(order="F"), 3)
    ZsU, iZs = np.unique(Zr, return_inverse=True)        # um material por impedância
    iZs = iZs.reshape((M, N), order="F") + 1

    ficheiro = os.path.join(rc.PASTA_VAL, rc.padrao_val(p, caso) + datetime.now().strftime("%Y%m%d_%H%M%S") + ".cst")
    cst = _cst()
    mws = _get(cst, "NewMWS")
    _H(mws, "define units", "With Units\n.Geometry \"mm\"\n.Frequency \"GHz\"\n.Time \"ns\"\nEnd With\n"
       f"Solver.FrequencyRange \"{rc.g(0.95*p.Freq)}\", \"{rc.g(1.05*p.Freq)}\"")
    vba = ""
    for im, Z in enumerate(ZsU, start=1):
        R_, X = Z.real, Z.imag
        fit = "".join(f'.AddTabulatedSurfaceImpedanceFittingValue "{rc.g(f)}", "{R_:.6g}", "{X:.6g}", "1.0"\n'
                      for f in (0.9 * p.Freq, p.Freq, 1.1 * p.Freq))
        a = (im * 0.37) % 1
        vba += (f'With Material\n.Reset\n.Name "zs_{im}"\n.FrqType "all"\n.Type "Lossy metal"\n'
                '.SetTabulatedSurfaceImpedanceModel "Opaque"\n' + fit +
                '.DispersiveFittingSchemeTabSI "Nth Order"\n.MaximalOrderNthModelFitTabSI "10"\n'
                '.ErrorLimitNthModelFitTabSI "0.1"\n.UseOnlyDataInSimFreqRangeNthModelTabSI "False"\n'
                f'.Colour "{0.2+0.6*a:.3f}", "0.500", "{0.9-0.6*a:.3f}"\n.Create\nEnd With\n')
    _H(mws, "define surface impedance materials", vba)
    vba = ""
    for i in range(1, M + 1):
        for j in range(1, N + 1):
            vba += (f'With Brick\n.Reset\n.Name "cel_{i}_{j}"\n.Component "RIS"\n.Material "zs_{iZs[i-1, j-1]}"\n'
                    f'.Xrange "{(j-1)*PxMM:.4f}", "{j*PxMM:.4f}"\n.Yrange "{(i-1)*PyMM:.4f}", "{i*PyMM:.4f}"\n'
                    f'.Zrange "0", "{rc.g(p.h_mm)}"\n.Create\nEnd With\n')
    vba += (f'With Brick\n.Reset\n.Name "GND"\n.Component "RIS"\n.Material "PEC"\n'
            f'.Xrange "0", "{N*PxMM:.4f}"\n.Yrange "0", "{M*PyMM:.4f}"\n.Zrange "{rc.g(-rc.TGND_MM)}", "0"\n'
            '.Create\nEnd With\n')
    _H(mws, "define cells", vba)

    _H(mws, "define boundaries, plane wave, farfield monitor, solver",
       _vba_onda_monitor(R) + 'ChangeSolverType "HF IntegralEq"')
    mws.SaveAs(ficheiro, "false")
    mws.RunSolver()                                       # solver de equação integral (I-solver)

    progresso(f'Caso "{caso}": a calcular o campo distante (corte e mapa 2D)...')
    ang, campo, azC, elC, mapa = _ler_farfield(mws, p.Freq, p.El_r)
    mws.Save()
    try:
        mws.Quit()                                        # fecha o projeto (fica gravado na pasta)
    except Exception:
        pass
    return rc.ValCST(caso, ang, campo, azC, elC, mapa, time.time() - t0, ficheiro, False)


def _mesmo_projeto(mws, ficheiro: str) -> bool:
    try:
        caminho = str(mws.GetProjectPath("Project"))
    except Exception:
        return False
    norm = lambda s: os.path.normcase(os.path.abspath(os.path.splitext(s)[0]))
    return norm(caminho) == norm(ficheiro)


def ler_existente(R: rc.Resultado, caso: str, ficheiro: str, progresso=print) -> rc.ValCST:
    t0 = time.time()
    progresso(f'A ler os resultados do caso "{caso}" do projeto CST existente...')
    cst = _cst()
    mws = cst.OpenFile(ficheiro)
    ja_aberto = False
    if mws is None:
        # O projeto já está aberto no CST (Model.lok): o OpenFile não devolve nada -> usar o projeto ativo
        try:
            mws = cst.Active3D()
        except Exception:
            mws = None
        if mws is None or not _mesmo_projeto(mws, ficheiro):
            raise RuntimeError(
                f'o CST não abriu o projeto "{os.path.basename(ficheiro)}". Se estiver aberto no CST, '
                'deixa-o como projeto ativo (janela da frente) ou fecha-o, e tenta de novo.')
        ja_aberto = True
    progresso(f'Caso "{caso}": a calcular a RCS (corte e mapa 2D)...')
    ang, campo, azC, elC, mapa = _ler_farfield(mws, R.p.Freq, R.p.El_r)
    if not ja_aberto:                                     # o projeto que o utilizador tem aberto fica como está
        mws.Save()
        try:
            mws.Quit()
        except Exception:
            pass
    return rc.ValCST(caso, ang, campo, azC, elC, mapa, time.time() - t0, ficheiro, True)


def abrir_projeto(ficheiro: str) -> str:
    """Abre um projeto existente no CST e deixa-o aberto."""
    _cst().OpenFile(ficheiro)
    return ficheiro


# ---- Opções da simulação com o dielétrico real ("Run CST Simulation") -----------------------------
# Solver: "TD" = Time Domain / T-solver (malha hexaédrica; pouca memória; com εr alto as células dentro do
#                dielétrico ficam pequenas -> passo de tempo pequeno e ressonâncias lentas a amortecer)
#         "FD" = Frequency Domain (malha tetraédrica do volume; muita memória: 10x10 ultrapassou os 32 GB)
#         "IE" = I-solver (equação integral: só malha as superfícies, sem ar; vale a pena testar num 5x5)
DIEL_SOLVER = "FD"              # config. 2 (o Contínuo 10x10 λ/2 correu até ao fim); "TD" é rápido na GPU mas parou sem atingir o steady state
# T-solver: banda do impulso (fator da frequência de trabalho). Banda larga -> impulso curto -> menos tempo
# simulado; só se usa o campo distante a 3,9 GHz (monitor). Materiais sem dispersão, por isso não há problema.
TD_BANDA = (0.5, 1.5)
TD_PASSOS_POR_LAMBDA = 10       # linhas da malha hexaédrica por comprimento de onda (no material)
TD_STEADY_STATE = "-30"         # critério de paragem pela energia [dB] (CST por defeito: -30; mais baixo = mais lento)
# Plano de simetria em y (magnético) quando a RIS e a onda plana são simétricas em y (incidência e reflexão
# com El = 0 e Az_i = 0: todas as linhas iguais, E segundo x). Simula metade -> ~metade da memória e do tempo,
# sem perder precisão. As fronteiras "expanded open" não mudam.
DIEL_SIMETRIA = True            # True: metade do modelo (Ysymmetry magnetic, plano y = 0; a RIS é criada
                                # centrada em y = 0 em _criar_geometria_diel). Teste Contínuo 10x10: 48 s, 10,7 GB
FD_PASSOS_POR_LAMBDA = 10       # passos por comprimento de onda (no material) da malha tetraédrica
FD_ORDEM = "Second"             # "Second" (mais preciso) ou "First" (3-4x menos memória, menos preciso)
FD_PRECISAO = None              # critério do solver iterativo; None = por defeito do CST (1e-4); ex.: "1e-3"
# Solver linear do FD. "Auto" = o CST tenta o iterativo e, se não convergir em 500 iterações, repete com o
# direto. Com estes contrastes de εr o iterativo estagnou SEMPRE (barra parada ~60 %, 60–90 % do tempo
# perdido) e todos os casos acabaram resolvidos pelo direto -> "Direct" vai logo ao direto.
FD_SOLVER_LINEAR = "Direct"     # "Direct" ou "Auto"
# Malha fixa (sem adaptação): com onda plana não há parâmetros S para o critério de paragem da adaptação.
# Cálculo remoto (CST Distributed Computing, "Remote calculation"): o solver corre na máquina do Main
# Controller configurado no CST (ADMINPC: RTX 5090, 16 threads, ~64 GB) e os resultados voltam para o projeto.
# Comandos copiados do CST: FD de Ref_FD_RemoteCalc.cst; TD do ValDiel_1bit_..._20261123_164806.
DIEL_REMOTO = True              # False: corre neste PC
TD_GPU = True                   # TD: aceleração por GPU (com DIEL_REMOTO: a GPU da máquina remota)
FD_GPU = True                   # FD: "Hardware acceleration" (1 GPU). Nota: o CST avisa que no FD só o solver de
                                # domain decomposition usa a GPU, por isso aqui não acelera nem poupa memória


def simetria_y(R: rc.Resultado) -> bool:
    """A RIS e a excitação são simétricas em relação ao plano do meio em y?"""
    p = R.p
    onda_ok = abs(R.vi) < 1e-9 and abs(R.kP[1]) < 1e-9                # sem componente y na incidência
    linhas_iguais = all(np.allclose(r.er, r.er[:1, :], equal_nan=True) for r in R.res)
    return bool(onda_ok and linhas_iguais and abs(R.vi + R.vr) < 1e-9 and abs(p.El_r) < 1e-9)


def _vba_simetria(R: rc.Resultado) -> str:
    """E segundo x é tangente ao plano y = const -> parede magnética (Ht = 0)."""
    if DIEL_SIMETRIA and simetria_y(R):
        return 'With Boundary\n.Ysymmetry "magnetic"\nEnd With\n'
    return ""


def nome_solver_diel() -> str:
    """Nome do solver do 'Run CST Simulation' (para as mensagens)."""
    nome = {"IE": "I-solver", "TD": "Time Domain (T-solver)"}.get(
        DIEL_SOLVER, f"Frequency Domain, {FD_ORDEM.lower()} order, solver "
                     + ("direto" if FD_SOLVER_LINEAR == "Direct" else "automático"))
    gpu = (DIEL_SOLVER == "FD" and FD_GPU) or (DIEL_SOLVER == "TD" and TD_GPU)
    return (nome + (", com GPU" if gpu else "")
            + (", na máquina remota" if DIEL_REMOTO and _vba_acelerar() else ""))


def _vba_acelerar() -> str:
    """Bloco 'acceleration' do CST (cálculo remoto e GPU), à parte do bloco do solver."""
    if DIEL_SOLVER == "FD" and (DIEL_REMOTO or FD_GPU):
        return ('With FDSolver\n'
                '     .MPIParallelization "False"\n'
                f'     .UseDistributedComputing "{DIEL_REMOTO}"\n'
                + ('     .NetworkComputingStrategy "RunRemote"\n'
                   '     .NetworkComputingJobCount "3"\n' if DIEL_REMOTO else '') +
                '     .UseParallelization "True"\n'
                '     .MaxCPUs "1024"\n'
                '     .MaximumNumberOfCPUDevices "2"\n'
                f'     .HardwareAcceleration "{FD_GPU}"\n'
                '     .MaximumNumberOfGPUs "1"\n'
                'End With')
    if DIEL_SOLVER == "TD" and (DIEL_REMOTO or TD_GPU):
        return ('With Solver\n'
                '     .UseParallelization "True"\n'
                '     .MaximumNumberOfThreads "1024"\n'
                '     .MaximumNumberOfCPUDevices "2"\n'
                f'     .RemoteCalculation "{DIEL_REMOTO}"\n'
                f'     .UseDistributedComputing "{DIEL_REMOTO}"\n'
                '     .MaxNumberOfDistributedComputingPorts "64"\n'
                '     .DistributeMatrixCalculation "True"\n'
                '     .MPIParallelization "False"\n'
                '     .AutomaticMPI "False"\n'
                '     .ConsiderOnly0D1DResultsForMPI "False"\n'
                f'     .HardwareAcceleration "{TD_GPU}"\n'
                '     .MaximumNumberOfGPUs "1"\n'
                'End With')
    return ""


def _vba_solver_diel(R: rc.Resultado) -> str:
    fs = rc.g(R.p.Freq)
    if DIEL_SOLVER == "IE":
        return 'ChangeSolverType "HF IntegralEq"'
    if DIEL_SOLVER == "TD":
        f1, f2 = TD_BANDA[0] * R.p.Freq, TD_BANDA[1] * R.p.Freq
        return (f'Solver.FrequencyRange "{rc.g(f1)}", "{rc.g(f2)}"\n'
                'ChangeSolverType "HF Time Domain"\n'
                'Mesh.SetCreator "High Frequency"\n'
                'With Mesh\n.MeshType "PBA"\nEnd With\n'
                f'With MeshSettings\n.SetMeshType "Hex"\n.Set "Version", 1%\n'
                f'.Set "StepsPerWaveNear", "{TD_PASSOS_POR_LAMBDA}"\n.Set "StepsPerWaveFar", "{TD_PASSOS_POR_LAMBDA}"\n'
                'End With\n'
                'With Solver\n.Method "Hexahedral"\n.CalculationType "TD-PLW"\n.StimulationPort "Plane wave"\n'
                f'.StimulationMode "1"\n.SteadyStateLimit "{TD_STEADY_STATE}"\n.MeshAdaption "False"\n'
                '.StoreTDResultsInCache "False"\n.RunDiscretizerOnly "False"\n.FullDeembedding "False"\n'
                '.SuperimposePLWExcitation "False"\n.UseSensitivityAnalysis "False"\nEnd With')
    return ('ChangeSolverType "HF Frequency Domain"\n'
            'Mesh.SetCreator "High Frequency"\n'
            'With Mesh\n.MeshType "Tetrahedral"\nEnd With\n'
            f'With MeshSettings\n.SetMeshType "Tet"\n.Set "StepsPerWaveNear", "{FD_PASSOS_POR_LAMBDA}"\n'
            f'.Set "StepsPerWaveFar", "{FD_PASSOS_POR_LAMBDA}"\nEnd With\n'
            'With FDSolver\n.Reset\n.SetMethod "Tetrahedral", "General purpose"\n'
            f'.OrderTet "{FD_ORDEM}"\n.Type "{FD_SOLVER_LINEAR}"\n'
            + (f'.AccuracyTet "{FD_PRECISAO}"\n' if FD_PRECISAO else '') +
            '.MeshAdaptionTet "False"\n'
            '.AddMonitorSamples "True"\n.ResetSampleIntervals "all"\n'
            f'.AddSampleInterval "{fs}", "{fs}", "1", "Single", "False"\nEnd With')


def _erros_log(ficheiro: str) -> str:
    """Mensagens '*** Error ***' do Result\\Model.log do projeto (e o aviso de memória, se houver)."""
    fl = os.path.join(os.path.splitext(ficheiro)[0], "Result", "Model.log")
    try:
        with open(fl, encoding="utf-8", errors="ignore") as f:
            linhas = f.read().splitlines()
    except OSError:
        return "O Model.log do projeto não foi encontrado (o solver pode não ter arrancado)."
    erros = []
    for i, l in enumerate(linhas):
        if "*** Error ***" in l:
            for m in linhas[i + 1:i + 4]:                 # a mensagem vem na(s) linha(s) seguinte(s)
                t = m.split("   ", 1)[-1].strip() if m[:4].isdigit() else m.strip()
                if t and not t.startswith("---"):
                    erros.append(t)
                    break
        elif "aborted by user" in l:
            erros.append("Solver parado pelo utilizador.")
    if not erros:
        return "O Model.log não tem erros: o solver pode ter sido parado ou o cálculo remoto não devolveu resultados."
    txt = "Erros do CST (Model.log):\n  • " + "\n  • ".join(dict.fromkeys(erros))
    if any("memory" in e.lower() for e in erros):
        txt += ("\nO modelo não cabe na memória da máquina que correu o solver. Reduz o problema (ex.: M×N menor, "
                "simetria em y) ou usa o Time Domain.")
    return txt


def _criar_geometria_diel(mws, R: rc.Resultado, q: int):
    """Array dielétrico: um material por εr (tipo Normal) em cada célula + plano de massa PEC."""
    p = R.p
    M, N = int(p.M), int(p.N)
    er_mat = R.res[q].er
    if np.isnan(er_mat).any():
        raise ValueError("A matriz de εr tem NaN: verifica a curva fase–εr.")
    PxMM, PyMM = p.Px * 1e3, p.Py * 1e3
    erU = np.unique(np.round(er_mat.ravel(), 3))
    vba = ""
    for im, e in enumerate(erU, start=1):
        a = (im * 0.37) % 1
        vba += (f'With Material\n.Reset\n.Name "er_{im}"\n.FrqType "all"\n.Type "Normal"\n.Epsilon "{e:.3f}"\n'
                f'.Mu "1.0"\n.TanD "{rc.g(rc.TAND)}"\n.TanDFreq "{rc.g(p.Freq)}"\n.TanDGiven "True"\n'
                f'.TanDModel "ConstTanD"\n.Colour "{0.2+0.6*a:.3f}", "0.500", "{0.9-0.6*a:.3f}"\n.Create\nEnd With\n')
    _H(mws, "define dielectric materials (er)", vba)
    # RIS centrada em y = 0: o plano de simetria do CST (Ysymmetry) está sempre em y = 0. Com a placa de
    # 0 a M·Py, a simetria espelhava-a e o CST simulava 2M linhas. Mover a placa não altera a RCS.
    y0 = -M * PyMM / 2
    vba = ""
    for i in range(1, M + 1):
        for j in range(1, N + 1):
            im = int(np.flatnonzero(erU == np.round(er_mat[i-1, j-1], 3))[0]) + 1
            vba += (f'With Brick\n.Reset\n.Name "cel_{i}_{j}"\n.Component "RIS"\n.Material "er_{im}"\n'
                    f'.Xrange "{(j-1)*PxMM:.4f}", "{j*PxMM:.4f}"\n'
                    f'.Yrange "{y0+(i-1)*PyMM:.4f}", "{y0+i*PyMM:.4f}"\n'
                    f'.Zrange "0", "{rc.g(p.h_mm)}"\n.Create\nEnd With\n')
    vba += (f'With Brick\n.Reset\n.Name "GND"\n.Component "RIS"\n.Material "PEC"\n.Xrange "0", "{N*PxMM:.4f}"\n'
            f'.Yrange "{y0:.4f}", "{-y0:.4f}"\n.Zrange "{rc.g(-rc.TGND_MM)}", "0"\n.Create\nEnd With\n')
    _H(mws, "define cells (dielectric)", vba)


def validar_dielectrico(R: rc.Resultado, caso: str, progresso=print) -> rc.ValCST:
    """'Run CST Simulation': RIS com o dielétrico real (εr por célula), onda plana, campo distante,
    Frequency Domain solver; lê o corte e o mapa 2D como na validação IBC."""
    p = R.p
    q = R.nomes.index(caso)
    t0 = time.time()
    os.makedirs(rc.PASTA_VAL, exist_ok=True)
    fs = rc.g(p.Freq)
    ficheiro = os.path.join(rc.PASTA_VAL, rc.padrao_val(p, caso, True, rc.PREF_DIEL)
                            + datetime.now().strftime("%Y%m%d_%H%M%S") + ".cst")
    progresso(f'Dielétrico real — "{caso}": a criar o modelo no CST...')
    mws = _get(_cst(), "NewMWS")
    _H(mws, "define units", 'With Units\n.Geometry "mm"\n.Frequency "GHz"\n.Time "ns"\nEnd With\n'
       f'Solver.FrequencyRange "{rc.g(0.95*p.Freq)}", "{rc.g(1.05*p.Freq)}"')
    _criar_geometria_diel(mws, R, q)
    _H(mws, "define boundaries, plane wave, farfield monitor", _vba_onda_monitor(R) + _vba_simetria(R))
    _H(mws, "define solver (dielectric model)", _vba_solver_diel(R))
    if _vba_acelerar():
        _H(mws, "define solver acceleration (remote calculation)", _vba_acelerar())
    mws.SaveAs(ficheiro, "false")
    nomeSolver = nome_solver_diel()
    txtSim = ", simetria em y: metade do modelo" if _vba_simetria(R) else ""
    progresso(f'Dielétrico real — "{caso}": a simular ({nomeSolver}{txtSim}; pode demorar bastante)...')
    mws.RunSolver()
    if not rc.tem_resultados(ficheiro):
        # O solver terminou sem campo distante (ex.: falta de memória): mostrar o erro do Model.log em vez
        # do "farfield ... is not selected" que o CST dá ao tentar ler um resultado que não existe.
        mws.Save()
        try:
            mws.Quit()
        except Exception:
            pass
        raise RuntimeError(f"a simulação terminou sem campo distante.\n{_erros_log(ficheiro)}\n"
                           f"Projeto: {os.path.basename(ficheiro)}")
    progresso(f'Dielétrico real — "{caso}": a calcular o campo distante (corte e mapa 2D)...')
    ang, campo, azC, elC, mapa = _ler_farfield(mws, p.Freq, p.El_r)
    mws.Save()
    try:
        mws.Quit()                                        # fecha o projeto (fica gravado na pasta)
    except Exception:
        pass
    return rc.ValCST(caso, ang, campo, azC, elC, mapa, time.time() - t0, ficheiro, False)
