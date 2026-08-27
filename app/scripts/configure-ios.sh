#!/usr/bin/env bash
#
# Ajustes que o `cap add ios` não faz e sem os quais o app não funciona.
#
# Roda depois de gerar o projeto Xcode, no runner macOS. Existe como script (e não
# como um `ios/` versionado) porque o projeto é desenvolvido no Windows: um `.xcodeproj`
# commitado viraria conflito a cada `cap sync` e ninguém aqui consegue abri-lo.
set -euo pipefail

PLIST="${1:-ios/App/App/Info.plist}"
if [[ ! -f "$PLIST" ]]; then
  echo "Não achei $PLIST — o 'cap add ios' rodou?" >&2
  exit 1
fi

set_string() {
  /usr/libexec/PlistBuddy -c "Delete :$1" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}

# Sem esta permissão o iOS devolve silêncio em vez de erro: o app não enxerga a TV e
# não há nada na tela explicando por quê. O texto é o que aparece no alerta do sistema.
set_string NSLocalNetworkUsageDescription \
  "O controle fala direto com a sua TV pelo Wi-Fi de casa. Sem esta permissão ele não consegue encontrá-la."

# A TV se anuncia por Bonjour; declarar os serviços é exigência do iOS 14+ para
# qualquer coisa que toque a rede local.
/usr/libexec/PlistBuddy -c "Delete :NSBonjourServices" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices:0 string _lg-remote._tcp" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices:1 string _airplay._tcp" "$PLIST"

# O plugin decide a confiança por host, no delegate. A exceção de ATS abaixo é só
# para o iOS não barrar a conexão ANTES de o delegate ser chamado — a validação
# continua acontecendo, só que na nossa mão.
/usr/libexec/PlistBuddy -c "Delete :NSAppTransportSecurity" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true" "$PLIST"

# A tela do controle é retrato; deitar não acrescenta nada e atrapalha o polegar.
/usr/libexec/PlistBuddy -c "Delete :UISupportedInterfaceOrientations" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :UISupportedInterfaceOrientations array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :UISupportedInterfaceOrientations:0 string UIInterfaceOrientationPortrait" "$PLIST"

echo "Info.plist configurado:"
/usr/libexec/PlistBuddy -c "Print" "$PLIST" | grep -A3 -E "LocalNetwork|Bonjour|AppTransport" || true

# --- ícone --------------------------------------------------------------
#
# Sem isto o app fica com o quadrado branco do template no telefone. O usuário
# identifica o app pelo ícone; um branco é indistinguível de "algo deu errado".
ICON_SRC="www/icons/icon-512.png"
ICON_DIR="ios/App/App/Assets.xcassets/AppIcon.appiconset"
if [[ -f "$ICON_SRC" && -d "$ICON_DIR" ]]; then
  # O Xcode moderno aceita um único 1024x1024 universal. O nosso maior é 512;
  # a ampliação perde nitidez, mas é o que existe — e é melhor que o branco.
  sips -s format png -z 1024 1024 "$ICON_SRC" --out "$ICON_DIR/AppIcon-512@2x.png" >/dev/null
  echo "Ícone instalado a partir de $ICON_SRC"
else
  echo "AVISO: não instalei ícone ($ICON_SRC ou $ICON_DIR não existe)." >&2
fi
