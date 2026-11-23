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
    valores = []
    for P in (ptsV, ptsM):
        ff.Reset()
        ff.SetPlotMode("Efield")
        ff.SetScaleLinear("True")
        for th, ph in P:
            ff.AddListEvaluationPoint(float(th), float(ph), 0, "spherical", "frequency", float(freq))
        ff.CalculateList(nomeFF)
        v = np.asarray(ff.GetList("Spherical abs"), float).ravel()
        valores.append(v[-len(P):])                      # só os pontos pedidos nesta lista
    return angV, valores[0], azC, elC, valores[1].reshape(forma, order="F")


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


def ler_existente(R: rc.Resultado, caso: str, ficheiro: str, progresso=print) -> rc.ValCST:
    t0 = time.time()
    progresso(f'A ler os resultados do caso "{caso}" do projeto CST existente...')
    mws = _cst().OpenFile(ficheiro)
    progresso(f'Caso "{caso}": a calcular o campo distante (corte e mapa 2D)...')
    ang, campo, azC, elC, mapa = _ler_farfield(mws, R.p.Freq, R.p.El_r)
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


# Frequency Domain solver (malha tetraédrica) para o modelo dielétrico. Malha fixa (sem adaptação):
# com onda plana não há parâmetros S para o critério de paragem da adaptação.
FD_PASSOS_POR_LAMBDA = 10       # passos por comprimento de onda (no material) da malha tetraédrica
FD_ORDEM = "Second"             # ordem dos elementos tetraédricos


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
    vba = ""
    for i in range(1, M + 1):
        for j in range(1, N + 1):
            im = int(np.flatnonzero(erU == np.round(er_mat[i-1, j-1], 3))[0]) + 1
            vba += (f'With Brick\n.Reset\n.Name "cel_{i}_{j}"\n.Component "RIS"\n.Material "er_{im}"\n'
                    f'.Xrange "{(j-1)*PxMM:.4f}", "{j*PxMM:.4f}"\n.Yrange "{(i-1)*PyMM:.4f}", "{i*PyMM:.4f}"\n'
                    f'.Zrange "0", "{rc.g(p.h_mm)}"\n.Create\nEnd With\n')
    vba += (f'With Brick\n.Reset\n.Name "GND"\n.Component "RIS"\n.Material "PEC"\n.Xrange "0", "{N*PxMM:.4f}"\n'
            f'.Yrange "0", "{M*PyMM:.4f}"\n.Zrange "{rc.g(-rc.TGND_MM)}", "0"\n.Create\nEnd With\n')
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
    _H(mws, "define boundaries, plane wave, farfield monitor", _vba_onda_monitor(R))
    _H(mws, "define frequency domain solver (tet, single frequency)",
       'ChangeSolverType "HF Frequency Domain"\n'
       'Mesh.SetCreator "High Frequency"\n'
       'With Mesh\n.MeshType "Tetrahedral"\nEnd With\n'
       f'With MeshSettings\n.SetMeshType "Tet"\n.Set "StepsPerWaveNear", "{FD_PASSOS_POR_LAMBDA}"\n'
       f'.Set "StepsPerWaveFar", "{FD_PASSOS_POR_LAMBDA}"\nEnd With\n'
       'With FDSolver\n.Reset\n.SetMethod "Tetrahedral", "General purpose"\n'
       f'.OrderTet "{FD_ORDEM}"\n.MeshAdaptionTet "False"\n.AddMonitorSamples "True"\n'
       '.ResetSampleIntervals "all"\n'
       f'.AddSampleInterval "{fs}", "{fs}", "1", "Single", "False"\nEnd With')
    mws.SaveAs(ficheiro, "false")
    progresso(f'Dielétrico real — "{caso}": a simular (Frequency Domain solver; pode demorar bastante)...')
    mws.RunSolver()
    progresso(f'Dielétrico real — "{caso}": a calcular o campo distante (corte e mapa 2D)...')
    ang, campo, azC, elC, mapa = _ler_farfield(mws, p.Freq, p.El_r)
    mws.Save()
    try:
        mws.Quit()                                        # fecha o projeto (fica gravado na pasta)
    except Exception:
        pass
    return rc.ValCST(caso, ang, campo, azC, elC, mapa, time.time() - t0, ficheiro, False)
