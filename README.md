# Cube Baby Editor

Editor de presets para o pedal **M-VAVE / Cuvave CUBE Baby** (versão para guitarra elétrica) no **Linux**.
Ajuste os knobs pelo computador, ouça na hora, grave nos presets A/B/C do pedal e guarde quantos sons
quiser no seu computador.

![Cube Baby Editor](docs/screenshot.png)

> ⚠️ Este app é para o **CUBE Baby elétrico** (o pedal se identifica com o id `0x4F06`).
> Para o **Cube Baby AC** (violão), use o [cubebaby-editor](https://github.com/walbertfreitas/cubebaby-editor).
> Software não oficial, sem ligação com a M-VAVE/Cuvave. Use por sua conta e risco.

---

## Instalação

### 1. Instale as dependências

Você precisa de **Python 3.10+**, **PyQt6** e **python-rtmidi**. Escolha o comando da sua distribuição:

| Distribuição | Comando |
|---|---|
| Arch, CachyOS, Manjaro, EndeavourOS | `sudo pacman -S --needed python-pyqt6 python-rtmidi git` |
| Ubuntu 24.04+, Debian 12+, Mint 22+, Pop!_OS, Zorin 17+ | `sudo apt install python3-pyqt6 python3-rtmidi git` |
| Fedora | `sudo dnf install python3-pyqt6 python3-rtmidi git` |
| openSUSE | `sudo zypper install python3-PyQt6 python3-python-rtmidi git` |

<details>
<summary>Minha distribuição não tem esses pacotes (usar pip)</summary>

```sh
python3 -m venv ~/.venvs/cubebaby
~/.venvs/cubebaby/bin/pip install PyQt6 python-rtmidi
# depois, rode sempre com:
~/.venvs/cubebaby/bin/python -m cubebaby_el
```
O `python-rtmidi` pode precisar dos pacotes de desenvolvimento do ALSA (`libasound2-dev` no Ubuntu,
`alsa-lib-devel` no Fedora) para compilar.
</details>

### 2. Baixe o editor

```sh
git clone https://github.com/GusCfer/CubeBaby-editor.git cube-baby-editor
cd cube-baby-editor
```

Sem git? Na página do projeto clique em **Code → Download ZIP**, extraia e abra um terminal dentro da pasta.

### 3. Abra

```sh
./cube-baby-editor
```

Para colocar o app no **menu de aplicativos**, rode uma vez:

```sh
./install.sh            # cria o atalho "Cube Baby Editor"
./install.sh --remove   # remove o atalho
```

### Atualizar

```sh
cd cube-baby-editor && git pull
```

---

## Como usar

1. Ligue o pedal e conecte no USB. Feche o CubeSuite e outros editores que estejam usando o pedal.
2. Abra o editor: ele conecta sozinho e faz um **backup automático** dos presets A/B/C.
3. Escolha o preset **A, B ou C** — e deixe o pedal no mesmo preset para ouvir as mudanças.
4. Ajuste os knobs. As alterações são aplicadas **ao vivo**, mas só ficam salvas no pedal quando você clicar
   em **Gravar no pedal**.

### Controles

| Ação | Como |
|---|---|
| Girar knob | arrastar para cima/baixo, roda do mouse ou setas ↑ ↓ (Shift+roda = passo maior) |
| Digitar valor | duplo clique no knob, ou selecionar o knob e digitar o número → **Enter** (Esc cancela) |
| Trocar preset | teclas **A / B / C**, **Ctrl+1/2/3** ou **F1/F2/F3** (Ctrl+PgUp/PgDn = anterior/próximo) |
| Ligar/desligar seção | chave no canto de cada cartão (Amp/Drive, Modulação & Delay, IR & Reverb) |
| Gravar no pedal | botão **Gravar no pedal** ou **Ctrl+S** |
| Desfazer edição | **Descartar alterações** volta ao que está gravado no pedal |

### Meus presets (no computador)

- **＋ Salvar som atual** guarda o som em `~/.local/share/cube-baby-editor/presets/`.
- **Duplo clique** num preset da lista carrega ele no preset atual (A/B/C) — depois é só gravar no pedal.
- Botão direito: substituir, renomear, exportar ou excluir. **Importar/Exportar** usam arquivos `.json`,
  fáceis de compartilhar.

### Backup

Cada conexão gera um backup em `~/.local/share/cube-baby-editor/backups/`. No menu **Backup** você pode
exportar o banco atual ou restaurar um backup no pedal.

### Valores

| Knob | Faixa | Observação |
|---|---|---|
| TYPE | Amp 1–9 | modelo de amplificador |
| GAIN | 0–7 | |
| TONE | 0–15 | |
| MOD | 0–15 | 0–6 chorus · 7–8 desligado · 9–15 phaser |
| TIME | 0–31 | tempo do delay |
| FB | 0–127 | repetições do delay |
| MIX | 0–118 | volume do delay |
| IR CAB | 0–8 | gabinete (IR) |
| REVERB | 0–15 | |
| VOLUME | 0–127 | |

---

## Problemas comuns

| Problema | Solução |
|---|---|
| "Pedal não encontrado" | Confira o cabo (alguns cabos USB só carregam, não transmitem dados). Rode `amidi -l`: deve aparecer `USB2.0 Device MIDI 1`. |
| "Sem resposta do pedal" | Feche outros programas que usam o pedal (CubeSuite no Wine, DAWs) e clique em **Conectar**. |
| "Modelo não suportado" | Seu pedal não é o CUBE Baby elétrico (`0x4F06`). Para o modelo AC use o cubebaby-editor. |
| Mexo na tela e o som não muda | Deixe o pedal no mesmo preset (A/B/C) selecionado no editor. |
| Girei um knob físico e a tela não mudou | É normal: o pedal não informa a posição dos knobs físicos ao computador. |
| `ModuleNotFoundError: PyQt6` / `rtmidi` | Instale as dependências do passo 1. |

---

## Segurança

O editor **só escreve na área de presets** (banco 5) do pedal. A área de firmware/IRs (banco 0) é bloqueada no
código, e o upload de IRs não está implementado — não há risco de "brickar" o pedal pelo editor.

## Detalhes técnicos

<details>
<summary>Protocolo e mapa de memória</summary>

O pedal é USB class-compliant (driver `snd-usb-audio`, nada para instalar). O editor conversa por **MIDI SysEx**:
quadro `00 59 | CMD | LEN(u24) | DATA | CHK` empacotado em 7 bits. Detalhes em
[cubebaby-editor/docs/PROTOCOL.md](https://github.com/walbertfreitas/cubebaby-editor/blob/main/docs/PROTOCOL.md).

Presets: banco 5, endereço 0, 3 × 16 bytes. Edição ao vivo: `0x80000000 + slot*16 + byte`.

| byte | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | TYPE | GAIN | TONE | REVERB | FB | VOLUME | TIME | MIX | MOD | IR CAB | IR on | MOD/DLY on | DRIVE on |

Estrutura do código:

| Arquivo | Função |
|---|---|
| `cubebaby_el/protocol.py` | quadros SysEx (empacotamento 7 bits, checksum) |
| `cubebaby_el/device.py` | conexão USB-MIDI, mapa de parâmetros, trava de segurança |
| `cubebaby_el/library.py` | presets e backups em JSON |
| `cubebaby_el/gui.py` | interface PyQt6 |
</details>

## Créditos

- [walbertfreitas/cubebaby-editor](https://github.com/walbertfreitas/cubebaby-editor) — transporte SysEx (MIT)
- [MrGariZack/cubecontrol](https://github.com/MrGariZack/cubecontrol) — mapa de parâmetros do CUBE Baby elétrico (MIT)

Licença: [MIT](LICENSE).
