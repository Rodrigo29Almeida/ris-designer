"""
Núcleo de cálculo da RIS teórica (tradução do corpo de CalculateButtonPushed do
Theoretical_RIS_Phase_calculator.mlapp, versão v8).

Fase por célula (lei de Snell generalizada) -> casos contínuo / 1..4 bits com a
constante de fase δ ótima -> εr por célula, diagramas (corte e mapa 2D), lobos,
impedâncias de superfície para o modelo CST e leitura dos resultados CST já gravados.
Sem interface: pode ser usado diretamente num script ou num notebook.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field

import numpy as np

# ----------------------------------------------------------------------------- pastas
# Tudo relativo à pasta do projeto:
#   Phase\                      curvas fase–εr da célula unitária (simulações Floquet no CST)
#   Estudo_Er\Validacao_CST\    projetos CST da validação (Val_*, ValDiel_*) e respetivos .mat
# A variável de ambiente ERIS_PASTA_ESTUDO_ER permite usar outra pasta Estudo_Er (ex.: uma já com simulações).
PASTA_APP = os.path.dirname(os.path.abspath(__file__))
PASTA_PHASE = os.path.join(PASTA_APP, "Phase")
PASTA_ESTUDO_ER = os.environ.get("ERIS_PASTA_ESTUDO_ER") or os.path.join(PASTA_APP, "Estudo_Er")
PASTA_VAL = os.path.join(PASTA_ESTUDO_ER, "Validacao_CST")            # projetos "Val_*.cst" (+ .mat)

# ----------------------------------------------------------------------------- constantes
C0 = 299792458.0
ETA0 = 376.730313
EPS = np.finfo(float).eps
NB_LIST = [1, 2, 3, 4, 0]       # ordem dos casos em toda a app: 1..4 bits e, por fim, o contínuo (0)
I_CONT = NB_LIST.index(0)       # índice do contínuo (referência das normalizações)
CASO_CST = I_CONT               # caso mostrado na consola
TGND_MM = 1.0                   # espessura do plano de massa [mm]
TAND = 0.0                      # tanδ usada na simulação da célula unitária


def sind(x): return np.sin(np.deg2rad(x))
def cosd(x): return np.cos(np.deg2rad(x))
def acosd(x): return np.rad2deg(np.arccos(x))
def asind(x): return np.rad2deg(np.arcsin(x))
def atan2d(y, x): return np.rad2deg(np.arctan2(y, x))


def mround(x):
    """round do MATLAB (metade afasta-se de zero; o numpy arredonda para par)."""
    x = np.asarray(x, float)
    return np.sign(x) * np.floor(np.abs(x) + 0.5)


def g(x) -> str:
    """sprintf('%g', x) do MATLAB."""
    return "%g" % x


def nome_caso(nb: int) -> str:
    return "Contínuo" if nb == 0 else ("1 bit" if nb == 1 else f"{nb} bits")


def tag_de(nome: str) -> str:
    """'Contínuo' -> 'Continuo', '2 bits' -> '2bits' (nomes dos ficheiros)."""
    return nome.replace(" ", "").replace("í", "i")


def dB(x, ref):
    """Para gráficos (corte a -40 dB)."""
    return np.maximum(20 * np.log10(np.asarray(x) / ref + EPS), -40)


def dBr(x, ref):
    """Para métricas (sem corte)."""
    return 20 * np.log10(np.asarray(x) / ref + EPS)


# ----------------------------------------------------------------------------- parâmetros
@dataclass
class Params:
    M: int = 10                 # linhas (y)
    N: int = 10                 # colunas (x)
    Px: float = 0.03843         # período em x [m] (λ/2 a 3,9 GHz)
    Py: float = 0.03843         # período em y [m] (λ/2 a 3,9 GHz)
    Freq: float = 3.9           # [GHz]
    h_mm: float = 1.524         # espessura do dielétrico [mm]
    Az_i: float = 0.0
    El_i: float = 0.0
    Az_r: float = 30.0
    El_r: float = 0.0
    usarDelta: bool = True


# ----------------------------------------------------------------------------- curva fase–εr
@dataclass
class Curva:
    ficheiro: str
    er: np.ndarray              # εr
    fase: np.ndarray            # fase relativa (unwrap, mínimo = 0) [°]
    amp: np.ndarray             # |S11|
    phMax: float
    aviso: str = ""             # preenchido se for usada a curva de incidência normal

    def clamp(self, P):
        """Fases fora da curva -> extremo mais próximo (360° = 0°)."""
        P = np.asarray(P, float)
        return P * (P <= self.phMax) + self.phMax * ((P > self.phMax) & ((P - self.phMax) <= (360 - P)))

    def _interp(self, P, y):
        x = self.fase
        o = np.argsort(x)
        P = np.asarray(P, float)
        r = np.interp(P, x[o], y[o])
        return np.where((P < x.min()) | (P > x.max()), np.nan, r)

    def fase2er(self, P):
        return self._interp(self.clamp(P), self.er)

    def fase2amp(self, P):
        return self._interp(self.clamp(P), self.amp)


def nomes_curva(p: Params):
    fTag = g(p.Freq).replace(".", "_")             # 3.9   -> 3_9
    hTag = g(p.h_mm).replace(".", "p")             # 1.524 -> 1p524
    thI = int(mround(acosd(np.clip(cosd(p.El_i) * cosd(p.Az_i), -1, 1))))
    if thI == 0:
        suf = ""
    elif abs(p.Az_i) < 1e-9:
        suf = f"_th{thI}_elev"                     # plano da elevação (pol. H = TE)
    else:
        suf = f"_th{thI}"                          # plano do azimute (pol. H = TM)
    return f"{fTag}_h{hTag}{suf}.txt", f"{fTag}_h{hTag}.txt", fTag, hTag, thI


def carregar_curva(p: Params, pasta: str = PASTA_PHASE) -> Curva:
    fich, fichNormal, _, _, thI = nomes_curva(p)
    aviso = ""
    if (not os.path.isfile(os.path.join(pasta, fich)) and fich != fichNormal
            and os.path.isfile(os.path.join(pasta, fichNormal))):
        aviso = (f"Não existe a curva {fich} (incidência de {thI}°). Vou usar a de incidência "
                 f"normal ({fichNormal}): os εr são uma aproximação.")
        fich = fichNormal
    dados = np.loadtxt(os.path.join(pasta, fich), comments="#", ndmin=2)
    er = dados[:, 0].copy()
    fase = dados[:, 1].copy()
    amp = dados[:, 2].copy() if dados.shape[1] >= 3 else np.ones(len(er))
    # Unwrap: a fase desce com εr; cada subida corresponde a um salto de 360°
    f = np.concatenate([[0], (np.diff(fase) > 0).astype(float)])
    fase = fase + 360 * (f.sum() - np.cumsum(f))
    fase = fase - fase.min()
    return Curva(fich, er, fase, amp, float(fase.max()), aviso)


# ----------------------------------------------------------------------------- resultados
@dataclass
class Caso:
    nome: str
    nbits: int
    desvio: float               # δ [°]
    perda: float                # perda por erro de fase [dB]
    faseIdeal: np.ndarray
    fase: np.ndarray            # fase realizável
    amp: np.ndarray
    er: np.ndarray
    erroMax: float
    nEr: int
    nAjust: int
    valores: str


@dataclass
class Lobo:
    pico: float
    angPico: float
    iL: int
    iR: int
    sll: float
    angSll: float
    nivelObj: float
    nivelEsp: float


@dataclass
class Resultado:
    p: Params
    curva: Curva
    lam: float
    k: float
    ui: float
    vi: float
    ur: float
    vr: float
    II: np.ndarray
    JJ: np.ndarray
    PHASE_deg: np.ndarray
    passoX: float
    passoY: float
    res: list
    angF: np.ndarray
    CUT: list
    refC: float
    lob: list
    azg: np.ndarray
    elg: np.ndarray
    MAP: list
    ref2: float
    ZsCasos: list
    kP: np.ndarray
    dRef: float
    az_s: float
    el_s: float
    espNoCorte: bool
    log: list = field(default_factory=list)
    cRCS: float = 0.0           # RCS [dB(mm²)] = 20·log10(|FR|·cosθ) + cRCS (ver rcs_const)

    @property
    def nomes(self):
        return [r.nome for r in self.res]


def soma_array(fase, amp, II, JJ, Px, Py, k, U, V, ui, vi):
    """Σ A·exp(j(φ + k(x(u+ui) + y(v+vi)))) para cada direção (U, V)."""
    U = np.asarray(U, float)
    V = np.asarray(V, float)
    S = np.zeros(U.shape, complex)
    for F, A, i, j in zip(fase.ravel(), amp.ravel(), II.ravel(), JJ.ravel()):
        S += A * np.exp(1j * (np.deg2rad(F) + k * (i * Px * (U + ui) + j * Py * (V + vi))))
    return S


def lobos_de(y, ang, alvo):
    """Lobo principal (máximo mais perto do objetivo), limites e maior lobo secundário."""
    y = np.asarray(y, float)
    ymax = y.max()
    loc = np.zeros(len(y), bool)
    loc[1:-1] = (y[1:-1] >= y[:-2]) & (y[1:-1] >= y[2:])
    cand = np.flatnonzero(loc & (y >= ymax - 0.1))
    if cand.size == 0:
        cand = np.array([int(np.argmax(y))])
    ip = int(cand[np.argmin(np.abs(ang[cand] - alvo))])
    iL = ip
    while iL > 0 and y[iL - 1] <= y[iL]:
        iL -= 1
    iR = ip
    while iR < len(y) - 1 and y[iR + 1] <= y[iR]:
        iR += 1
    ys = y.copy()
    ys[iL:iR + 1] = -np.inf
    isl = int(np.argmax(ys))
    return ip, iL, iR, isl


def calcular(p: Params, pasta_phase: str = PASTA_PHASE) -> Resultado:
    log = []
    pr = log.append
    M, N = int(p.M), int(p.N)
    lam = C0 / (p.Freq * 1e9)
    k = 2 * np.pi / lam

    pr("### DADOS INICIAIS ###")
    pr(f"» Frequência: {g(p.Freq)} GHz  (λ = {lam*1e3:.1f} mm)")
    pr(f"» Array: {M} linhas × {N} colunas")
    pr(f"» Célula: {g(p.Px*1e3)} × {g(p.Py*1e3)} mm   |   Placa: {g(N*p.Px*1e3)} × {g(M*p.Py*1e3)} mm")
    pr(f"» Incidência: Az = {g(p.Az_i)}°, El = {g(p.El_i)}°   |   Reflexão: Az = {g(p.Az_r)}°, El = {g(p.El_r)}°\n")

    # ---- fase ideal
    ui = float(cosd(p.El_i) * sind(p.Az_i)); vi = float(sind(p.El_i))
    ur = float(cosd(p.El_r) * sind(p.Az_r)); vr = float(sind(p.El_r))
    II, JJ = np.meshgrid(np.arange(1, N + 1), np.arange(1, M + 1))
    PH = np.rad2deg(-k * (II * p.Px * (ui + ur) + JJ * p.Py * (vi + vr)))
    PH = np.mod(PH - PH.min(), 360)
    passoX = float(np.rad2deg(k * p.Px * abs(ui + ur)))
    passoY = float(np.rad2deg(k * p.Py * abs(vi + vr)))

    # ---- curva
    cv = carregar_curva(p, pasta_phase)

    # ---- casos (δ ótimo por caso)
    desvios = np.arange(0, 360, 0.5) if p.usarDelta else np.array([0.0])
    res = []
    for nb in NB_LIST:
        nome = nome_caso(nb)
        melhor = -np.inf
        for dlt in desvios:
            Fi = np.mod(PH + dlt, 360)
            if nb == 0:
                Fs = Fi
            else:
                passo = 360 / 2 ** nb
                Fs = np.mod(mround(Fi / passo) * passo, 360)
            Fr = cv.clamp(Fs)
            A = cv.fase2amp(Fr)
            eta = abs(np.sum(A * np.exp(1j * np.deg2rad(Fr - Fi)))) ** 2 / Fi.size ** 2
            if eta > melhor + 1e-12:                       # em empate fica o menor δ
                melhor = eta; dBest = float(dlt)
                FiB, FrB, AB, nFora = Fi, Fr, A, int(np.count_nonzero(Fs > cv.phMax))
        ERq = cv.fase2er(FrB)
        erOK = np.unique(np.round(ERq[~np.isnan(ERq)], 2))
        erroMax = float(np.max(np.abs(np.mod(FiB - FrB + 180, 360) - 180)))
        res.append(Caso(nome, nb, dBest, float(10 * np.log10(melhor)), FiB, FrB, AB, ERq, erroMax,
                        int(erOK.size), nFora, "—" if nb == 0 else ", ".join(f"{v:.1f}" for v in erOK)))
        pr(f"» {nome:<8} δ = {dBest:5.1f}°   perda de fase = {res[-1].perda:6.3f} dB   "
           f"erro máx = {erroMax:5.1f}°   células ajustadas = {nFora}")

    # ---- corte em azimute (El = El_r)
    az_s, el_s = 0.0 - p.Az_i, 0.0 - p.El_i         # especular (0.0 - x evita "-0")
    espNoCorte = abs(p.El_r - el_s) < 1e-9
    angF = np.arange(-90, 90 + 0.125, 0.25)
    uF = cosd(p.El_r) * sind(angF)
    vF = sind(p.El_r) * np.ones_like(angF)
    feF = np.maximum(cosd(p.El_r) * cosd(angF), 1e-6)
    CUT = [np.abs(soma_array(r.fase, r.amp, II, JJ, p.Px, p.Py, k, uF, vF, ui, vi)) * feF for r in res]
    refC = float(CUT[I_CONT].max())                # normalizado ao contínuo

    iObj = int(np.argmin(np.abs(angF - p.Az_r)))
    iEsp = int(np.argmin(np.abs(angF - az_s)))
    lob = []
    for q in range(len(res)):
        y = dBr(CUT[q], refC)
        ip, iL, iR, isl = lobos_de(y, angF, p.Az_r)
        lob.append(Lobo(float(y[ip]), float(angF[ip]), iL, iR, float(y[isl] - y[ip]), float(angF[isl]),
                        float(y[iObj]), float(y[iEsp]) if espNoCorte else np.nan))

    # ---- mapa 2D (Az x El, passo 1°)
    azg = np.arange(-90, 91, 1.0); elg = np.arange(-90, 91, 1.0)
    AZ, EL = np.meshgrid(azg, elg)
    U = cosd(EL) * sind(AZ); V = sind(EL)
    FE = np.maximum(cosd(EL) * cosd(AZ), 1e-6)
    MAP = [np.abs(soma_array(r.fase, r.amp, II, JJ, p.Px, p.Py, k, U, V, ui, vi)) * FE for r in res]
    ref2 = float(MAP[I_CONT].max())

    # ---- impedâncias de superfície (modelo CST)
    erC = cv.er
    ZsTL = 1j * ETA0 / np.sqrt(erC) * np.tan(k * np.sqrt(erC) * p.h_mm * 1e-3)
    fTL = np.rad2deg(np.angle((ZsTL - ETA0) / (ZsTL + ETA0)))
    dRef = float(np.rad2deg(np.angle(np.mean(np.exp(1j * np.deg2rad(fTL - cv.fase))))))
    ZsCasos = []
    for r in res:
        Gc = r.amp * np.exp(1j * np.deg2rad(r.fase + dRef))
        Z = ETA0 * (1 + Gc) / (1 - Gc + EPS)
        ZsCasos.append(np.maximum(Z.real, 0) + 1j * np.clip(Z.imag, -1e6, 1e6))
    wi = np.sqrt(max(1 - ui ** 2 - vi ** 2, 0))
    kP = -np.array([ui, vi, wi])

    cR = rcs_const(p.Px, p.Py, lam)
    pr(f"» RCS (teoria, ótica física): pico do contínuo = {20*np.log10(ref2) + cR:.1f} dB(mm²) "
       f"(placa {g(N*p.Px*1e3)} × {g(M*p.Py*1e3)} mm em incidência normal: "
       f"{10*np.log10(4*np.pi*(N*p.Px*M*p.Py/lam)**2*1e6):.1f} dB(mm²))")
    return Resultado(p, cv, lam, k, ui, vi, ur, vr, II, JJ, PH, passoX, passoY, res, angF, CUT, refC,
                     lob, azg, elg, MAP, ref2, ZsCasos, kP, dRef, az_s, el_s, espNoCorte, log, cR)


def rcs_const(Px: float, Py: float, lam: float) -> float:
    """Constante da RCS teórica [dB(mm²)].
    Ótica física: cada célula (área a = Px·Py) com coeficiente de reflexão Γ = A·e^{jφ} espalha, a 1 m,
    E = (a/λ)·Γ·cosθ·Eᵢ; somando as células, |E|/|Eᵢ| = (a/λ)·|FR|·cosθ e
        σ = 4π·r²·|E|²/|Eᵢ|² = 4π·(a/λ)²·(|FR|·cosθ)²   [m²].
    Em dB(mm²): σ = 20·log10(|FR|·cosθ) + 10·log10(4π·(a/λ)²·10⁶).
    (Placa uniforme em incidência normal: |FR| = M·N -> σ = 4π·A²/λ², a RCS especular clássica.)"""
    return float(10 * np.log10(4 * np.pi * (Px * Py / lam) ** 2 * 1e6))


# ----------------------------------------------------------------------------- CST: assinatura e ficheiros
def assinatura(p: Params, ficheiroCurva: str) -> str:
    return "|".join([str(int(p.M)), str(int(p.N)), g(p.Px), g(p.Py), g(p.Freq), g(p.h_mm), g(p.Az_i),
                     g(p.El_i), g(p.Az_r), g(p.El_r), str(int(bool(p.usarDelta))), ficheiroCurva])


def tag_celula(p: Params) -> str:
    """Tamanho da célula para os nomes: 25 x 25 mm -> 'P25x25mm'; 38.43 mm -> 'P38p43x38p43mm'."""
    t = lambda v: ("%.6g" % round(v * 1e3, 4)).replace(".", "p")
    return f"P{t(p.Px)}x{t(p.Py)}mm"


PREF_IBC = "Val_"          # validação com impedância de superfície ("Simular no CST")
PREF_DIEL = "ValDiel_"     # validação com o dielétrico real ("Run CST Simulation")


def padrao_val(p: Params, nome: str, com_tamanho: bool = True, prefixo: str = PREF_IBC) -> str:
    """Início do nome dos projetos de validação. com_tamanho=False: nomes antigos (sem o tamanho da célula)."""
    _, _, fTag, hTag, _ = nomes_curva(p)
    cel = f"_{tag_celula(p)}" if com_tamanho else ""
    return f"{prefixo}{tag_de(nome)}_{int(p.M)}x{int(p.N)}{cel}_{fTag}GHz_h{hTag}_Az{g(p.Az_r)}_El{g(p.El_r)}_"


GRANDEZA_MAT = "RCS_m2"   # grandeza dos .mat atuais (campo/mapCampo = RCS linear em m²); sem ela = |E| antigo


@dataclass
class ValCST:
    """Resultado CST em memória. campo/mapCampo = √(RCS em mm²), ou seja 20·log10(campo) = RCS [dB(mm²)]
    (assim as razões entre valores continuam a ser razões de campo, como na teoria)."""
    caso: str
    ang: np.ndarray
    campo: np.ndarray
    mapAz: np.ndarray
    mapEl: np.ndarray
    mapCampo: np.ndarray
    tempo: float
    ficheiro: str
    legado: bool


def ler_mats(assin: str, nomes, pasta: str = PASTA_VAL, prefixo: str = PREF_IBC) -> dict:
    """Resultados já extraídos (<prefixo>*.mat) com a mesma assinatura -> {caso: ValCST} (o mais recente ganha)."""
    import scipy.io as sio
    out = {}
    if not os.path.isdir(pasta):
        return out
    mats = sorted(glob.glob(os.path.join(pasta, prefixo + "*.mat")), key=os.path.getmtime)
    for fm in mats:
        try:
            r = sio.loadmat(fm, squeeze_me=True, struct_as_record=False)["reg"]
            if str(r.assinatura) != assin or str(r.caso) not in nomes:
                continue
            if str(getattr(r, "grandeza", "")) != GRANDEZA_MAT:
                continue                                  # .mat antigo (|E| em V/m): é preciso ler de novo
            amp = lambda x: np.sqrt(np.maximum(x, 0) * 1e6)          # RCS m² -> √(mm²)
            out[str(r.caso)] = ValCST(str(r.caso), np.atleast_1d(r.ang).astype(float),
                                      amp(np.atleast_1d(r.campo).astype(float)),
                                      np.atleast_1d(r.mapAz).astype(float), np.atleast_1d(r.mapEl).astype(float),
                                      amp(np.atleast_2d(r.mapCampo).astype(float)), float(r.tempo), str(r.ficheiro),
                                      bool(r.legado))
        except Exception:
            pass
    return out


def mats_antigos(assin: str, nomes, pasta: str = PASTA_VAL, prefixo: str = PREF_DIEL) -> dict:
    """Casos com .mat antigo (|E|, sem RCS) cujo projeto .cst ainda tem resultados -> {caso: ficheiro .cst}.
    Servem para voltar a ler esses projetos em RCS."""
    import scipy.io as sio
    out = {}
    if not os.path.isdir(pasta):
        return out
    for fm in sorted(glob.glob(os.path.join(pasta, prefixo + "*.mat")), key=os.path.getmtime):
        try:
            r = sio.loadmat(fm, squeeze_me=True, struct_as_record=False)["reg"]
            if (str(r.assinatura) != assin or str(r.caso) not in nomes
                    or str(getattr(r, "grandeza", "")) == GRANDEZA_MAT):
                continue
            fc = os.path.splitext(fm)[0] + ".cst"
            if os.path.isfile(fc) and tem_resultados(fc):
                out[str(r.caso)] = fc
        except Exception:
            pass
    return out


def tempos_historicos(M: int, N: int, prefixo: str = PREF_IBC, pasta: str = None) -> list:
    """Durações [s] de simulações anteriores (não as leituras de projetos existentes) com o mesmo M x N,
    a partir dos .mat gravados. Servem para estimar o tempo de espera."""
    import scipy.io as sio
    pasta = pasta or PASTA_VAL
    out = []
    for fm in glob.glob(os.path.join(pasta, f"{prefixo}*_{int(M)}x{int(N)}_*.mat")):
        try:
            r = sio.loadmat(fm, squeeze_me=True, struct_as_record=False, variable_names=["reg"])["reg"]
            if not bool(r.legado) and float(r.tempo) > 0:
                out.append(float(r.tempo))
        except Exception:
            pass
    return out


def gravar_mat(v: ValCST, assin: str):
    """Grava <projeto>.mat ao lado do .cst (struct 'reg'); campo e mapCampo = RCS linear em m².
    Se já existir um .mat antigo (|E|), não é apagado: passa a <projeto>_Efield.mat, e o tempo da simulação
    original é mantido (uma releitura não conta como simulação)."""
    import scipy.io as sio
    fm = os.path.splitext(v.ficheiro)[0] + ".mat"
    tempo, legado = float(v.tempo), bool(v.legado)
    if os.path.isfile(fm):
        try:
            r = sio.loadmat(fm, squeeze_me=True, struct_as_record=False)["reg"]
            if str(getattr(r, "grandeza", "")) != GRANDEZA_MAT:
                if legado and not bool(r.legado):
                    tempo, legado = float(r.tempo), False
                bak = os.path.splitext(fm)[0] + "_Efield.mat"
                if not os.path.exists(bak):
                    os.replace(fm, bak)
        except Exception:
            pass
    sig = lambda x: np.asarray(x, float) ** 2 / 1e6                # √(mm²) -> m²
    reg = {"caso": v.caso, "ang": np.asarray(v.ang, float).reshape(1, -1),
           "campo": sig(v.campo).reshape(1, -1),
           "mapAz": np.asarray(v.mapAz, float).reshape(1, -1), "mapEl": np.asarray(v.mapEl, float).reshape(1, -1),
           "mapCampo": sig(v.mapCampo), "tempo": tempo, "ficheiro": v.ficheiro,
           "legado": legado, "assinatura": assin, "grandeza": GRANDEZA_MAT}
    sio.savemat(fm, {"reg": reg})


def projeto_compativel(fich_cst: str, R: Resultado, q: int, tol_deg: float = 0.5) -> bool:
    """Confirma no ModelHistory.json do projeto que é o mesmo caso: mesma onda plana e
    mesma reflexão em todas as células (apanha diferenças de incidência, δ, curva, ...)."""
    M, N = int(R.p.M), int(R.p.N)
    fh = os.path.join(os.path.splitext(fich_cst)[0], "Model", "3D", "ModelHistory.json")
    if not os.path.isfile(fh):
        return False
    try:
        with open(fh, encoding="utf-8") as f:
            H = json.load(f)
        hs = H["history"]
        if isinstance(hs, dict):
            hs = [hs]
        txt = "\n".join("\n".join(h["code"]) if isinstance(h["code"], list) else str(h["code"]) for h in hs)
        # Tamanho da célula (os nomes antigos não o têm): a célula (1,1) tem de medir Px x Py
        tg = re.search(r'Name "cel_1_1".*?\.Xrange "([^"]*)", "([^"]*)"\s*\.Yrange "([^"]*)", "([^"]*)"', txt, re.S)
        if not tg:
            return False
        x1, x2, y1, y2 = (float(v) for v in tg.groups())
        if abs((x2 - x1) - R.p.Px * 1e3) > 1e-3 or abs((y2 - y1) - R.p.Py * 1e3) > 1e-3:
            return False
        tn = re.search(r'\.Normal "([^"]*)", "([^"]*)", "([^"]*)"', txt)
        if not tn or np.linalg.norm(np.array([float(x) for x in tn.groups()]) - R.kP) > 1e-3:
            return False
        tm = re.findall(r'Name "zs_(\d+)".*?FittingValue "[^"]*", "([^"]*)", "([^"]*)"', txt, re.S)
        tcl = re.findall(r'Name "cel_(\d+)_(\d+)"\s*\.Component "[^"]*"\s*\.Material "zs_(\d+)"', txt)
        if len(tcl) != M * N:
            return False
        zMat = {int(a): complex(float(b), float(c)) for a, b, c in tm}
        ZsF = np.full((M, N), np.nan, complex)
        for i, j, m in tcl:
            ZsF[int(i) - 1, int(j) - 1] = zMat[int(m)]
        Gf = (ZsF - ETA0) / (ZsF + ETA0)
        Ge = (R.ZsCasos[q] - ETA0) / (R.ZsCasos[q] + ETA0)
        return bool(np.all(np.abs(np.mod(np.rad2deg(np.angle(Gf / Ge)) + 180, 360) - 180) < tol_deg))
    except Exception:
        return False


def procurar_projetos(R: Resultado, ja: dict, pasta: str = PASTA_VAL):
    """Para "Carregar do CST": devolve ([(caso, ficheiro)], [casos sem projeto compatível])."""
    jobs, sem = [], []
    if not os.path.isdir(pasta):
        return jobs, sem
    for q, nome in enumerate(R.nomes):
        if nome in ja:
            continue
        # nomes novos (com o tamanho da célula) e antigos (sem); o tamanho é sempre confirmado no histórico
        fc = [f for cel in (True, False)
              for f in glob.glob(os.path.join(pasta, padrao_val(R.p, nome, cel) + "*.cst"))]
        if not fc:
            continue
        fc.sort(key=os.path.getmtime, reverse=True)
        escolhido = next((f for f in fc if tem_resultados(f) and projeto_compativel(f, R, q)), None)
        if escolhido is None:
            sem.append(nome)
        else:
            jobs.append((nome, escolhido))
    return jobs, sem


def tem_resultados(fich_cst: str) -> bool:
    """O projeto chegou ao fim da simulação? (tem o campo distante da onda plana em Result\\).
    Projetos interrompidos (solver parado, app fechada) não têm e não devem ser reaproveitados."""
    base = os.path.splitext(fich_cst)[0]
    return bool(glob.glob(os.path.join(glob.escape(base), "Result", "farfield*_pw.ffm")))


def _texto_historico(fich_cst: str):
    fh = os.path.join(os.path.splitext(fich_cst)[0], "Model", "3D", "ModelHistory.json")
    if not os.path.isfile(fh):
        return None
    with open(fh, encoding="utf-8") as f:
        hs = json.load(f)["history"]
    if isinstance(hs, dict):
        hs = [hs]
    return "\n".join("\n".join(h["code"]) if isinstance(h["code"], list) else str(h["code"]) for h in hs)


def array_compativel(fich_cst: str, R: Resultado, q: int = CASO_CST, com_onda: bool = False) -> bool:
    """Projeto dielétrico ('Array_*' ou 'ValDiel_*') igual ao que seria criado agora: mesmas M x N células,
    mesmo tamanho de célula, mesma espessura h e o mesmo εr (3 casas decimais) em todas as células.
    com_onda=True: também a mesma direção da onda plana (projetos simulados)."""
    M, N = int(R.p.M), int(R.p.N)
    try:
        txt = _texto_historico(fich_cst)
        if txt is None:
            return False
        if com_onda:
            tn = re.search(r'\.Normal "([^"]*)", "([^"]*)", "([^"]*)"', txt)
            if not tn or np.linalg.norm(np.array([float(x) for x in tn.groups()]) - R.kP) > 1e-3:
                return False
        mats = {m: float(e) for m, e in re.findall(r'\.Name "(er_\d+)".*?\.Epsilon "([^"]*)"', txt, re.S)}
        cel = re.findall(r'\.Name "cel_(\d+)_(\d+)"\s*\.Component "[^"]*"\s*\.Material "(er_\d+)"\s*'
                         r'\.Xrange "([^"]*)", "([^"]*)"\s*\.Yrange "([^"]*)", "([^"]*)"\s*\.Zrange "([^"]*)", "([^"]*)"',
                         txt)
        if len(cel) != M * N:
            return False
        er = np.round(R.res[q].er, 3)
        for i, j, m, x1, x2, y1, y2, z1, z2 in cel:
            i, j = int(i), int(j)
            if i > M or j > N or m not in mats or abs(mats[m] - er[i - 1, j - 1]) > 1e-3:
                return False
            if (abs(float(x2) - float(x1) - R.p.Px * 1e3) > 1e-3 or abs(float(y2) - float(y1) - R.p.Py * 1e3) > 1e-3
                    or abs(abs(float(z2) - float(z1)) - R.p.h_mm) > 1e-6):
                return False
        return True
    except Exception:
        return False


def procurar_array(R: Resultado, q: int = CASO_CST, pasta: str = PASTA_ESTUDO_ER):
    """Projeto 'Array_*' já existente e compatível (o mais recente), ou None."""
    fstr = g(R.p.Freq).replace(".", "G")
    if "G" not in fstr:
        fstr += "G"
    fc = glob.glob(os.path.join(pasta, f"Array_{int(R.p.M)}x{int(R.p.N)}_*{fstr}_*.cst"))
    fc.sort(key=os.path.getmtime, reverse=True)
    return next((f for f in fc if array_compativel(f, R, q)), None)


def procurar_diel(R: Resultado, q: int, pasta: str = PASTA_VAL):
    """Projeto 'ValDiel_*' (dielétrico real, simulado) compatível com o caso q (o mais recente), ou None."""
    fc = glob.glob(os.path.join(pasta, padrao_val(R.p, R.nomes[q], True, PREF_DIEL) + "*.cst"))
    fc.sort(key=os.path.getmtime, reverse=True)
    return next((f for f in fc if tem_resultados(f) and array_compativel(f, R, q, com_onda=True)), None)


def pontos_farfield(El_r: float):
    """Pontos (θ, φ) do campo distante: corte (El = El_r, passo 0.5°) e mapa Az x El (passo 2°).
    A ordem do mapa é a do MATLAB (coluna a coluna) -> reshape com order='F'."""
    angV = np.arange(-90, 90.25, 0.5)
    thV = acosd(np.clip(cosd(El_r) * cosd(angV), -1, 1))
    phV = np.mod(atan2d(sind(El_r) * np.ones_like(angV), cosd(El_r) * sind(angV)), 360)
    azC = np.arange(-90, 91, 2.0); elC = np.arange(-90, 91, 2.0)
    AZc, ELc = np.meshgrid(azC, elC)
    a = AZc.ravel(order="F"); e = ELc.ravel(order="F")
    thM = acosd(np.clip(cosd(e) * cosd(a), -1, 1))
    phM = np.mod(atan2d(sind(e), cosd(e) * sind(a)), 360)
    return angV, np.c_[thV, phV], azC, elC, np.c_[thM, phM], AZc.shape
