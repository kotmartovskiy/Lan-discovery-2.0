#!/bin/bash
# Сборка DVB-модулей (dvb-core, cx231xx, mn88473, lgdt3305, mb86a20s, xc5000,
# tda18271, tveeprom, cx2341x) из полного исходника ядра БЕЗ пересборки ядра.
# Для X96 Max / Armbian 6.18.51-ophub. Запуск от root, НА СЕРВЕРЕ.
#
# Зачем: в оphub-ядре DVB-подсистема выключена (нет CONFIG_DVB_*), заголовки
# содержат только headers. Собираем модули M= с копией рабочего .config и
# переопределениями CONFIG_*=m в командной строке make (kconfig при
# olddefconfig/syncconfig поднимает DVB_CORE/x264-тюнеры в y — модульная
# сборка требует m).
#
# После ОБНОВЛЕНИЯ ядра (новый vermagic) — перезапустить скрипт.
set -euo pipefail

KVER=6.18.51
KREL=$KVER-ophub
SRC=/usr/src/linux-$KVER
HDR=/usr/src/linux-headers-$KREL
URL=https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-$KVER.tar.xz
OUT=/lib/modules/$KREL/updates/dvb
SV=$SRC/Module.symvers

[ "$(id -u)" = 0 ] || { echo "Запусти от root"; exit 1; }

if [ ! -d "$SRC" ]; then
  echo "==> download + extract $URL"
  curl -fL --max-time 560 -o /tmp/linux-$KVER.tar.xz "$URL"
  tar -C /usr/src -xf /tmp/linux-$KVER.tar.xz
fi

echo "==> prepare .config (из рабочего ядра + Module.symvers из заголовков)"
cp /boot/config-$KREL $SRC/.config
cp $HDR/Module.symvers $SRC/
cd $SRC

./scripts/config --enable MEDIA_DIGITAL_TV_SUPPORT \
  --module DVB_CORE --module VIDEO_CX231XX --module VIDEO_CX231XX_DVB \
  --module DVB_MN88473
make olddefconfig

if [ ! -f /usr/include/libelf.h ]; then
  echo "==> install build deps"
  apt-get install -y libelf-dev libssl-dev
fi

echo "==> modules_prepare (без него нет scripts/module.lds — .ko не слинкуются)"
make modules_prepare

OVR="CONFIG_DVB_CORE=m CONFIG_MEDIA_TUNER_XC5000=m CONFIG_MEDIA_TUNER_TDA18271=m"

echo "==> dvb-core"
make -j4 M=drivers/media/dvb-core $OVR modules

echo "==> common (tveeprom, cx2341x)"
make -j4 M=drivers/media/common modules

echo "==> tuners (xc5000, tda18271)"
make -j4 M=drivers/media/tuners $OVR modules

echo "==> dvb-frontends (mn88473, lgdt3305, mb86a20s)"
make -j4 M=drivers/media/dvb-frontends CONFIG_DVB_CORE=m \
  KBUILD_EXTRA_SYMBOLS=$SV modules

echo "==> cx231xx"
make -j4 M=drivers/media/usb/cx231xx CONFIG_DVB_CORE=m \
  KBUILD_EXTRA_SYMBOLS="$SV $SRC/drivers/media/tuners/Module.symvers $SRC/drivers/media/dvb-frontends/Module.symvers $SRC/drivers/media/common/Module.symvers" \
  modules

echo "==> install + depmod"
mkdir -p $OUT
cp drivers/media/dvb-core/dvb-core.ko \
   drivers/media/common/tveeprom.ko \
   drivers/media/common/cx2341x.ko \
   drivers/media/tuners/xc5000.ko \
   drivers/media/tuners/tda18271.ko \
   drivers/media/dvb-frontends/mn88473.ko \
   drivers/media/dvb-frontends/lgdt3305.ko \
   drivers/media/dvb-frontends/mb86a20s.ko \
   drivers/media/usb/cx231xx/cx231xx.ko \
   $OUT/
depmod -a $KREL

modinfo $OUT/cx231xx.ko | grep -E '^(depends|vermagic)'
echo "OK: модули в $OUT (USB-алиасы cx231xx подхватятся при вставке тюнера)"
