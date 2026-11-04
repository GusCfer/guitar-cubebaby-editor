#!/bin/sh
# Adiciona (ou remove, com --remove) o Cube Baby Editor ao menu de aplicativos do usuário.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP="$APPS/cube-baby-editor.desktop"

if [ "$1" = "--remove" ]; then
    rm -f "$DESKTOP"
    echo "Atalho removido. (Seus presets em ~/.local/share/cube-baby-editor/ foram mantidos.)"
    exit 0
fi

if ! python3 -c "import PyQt6, rtmidi" 2>/dev/null; then
    echo "Faltam dependências (PyQt6 e/ou python-rtmidi). Veja a seção 'Instalação' do README."
    exit 1
fi

chmod +x "$DIR/cube-baby-editor"
mkdir -p "$APPS"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Cube Baby Editor
Comment=Editor de presets do M-VAVE/Cuvave CUBE Baby
Exec="$DIR/cube-baby-editor"
Icon=$DIR/cubebaby_el/assets/icon.svg
Terminal=false
Categories=AudioVideo;Audio;Music;
EOF
echo "Pronto! Procure \"Cube Baby Editor\" no menu de aplicativos."
