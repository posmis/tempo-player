# tempo-player

Python-плеер для **speed up / slowed** музыки с изменением скорости прямо во время воспроизведения.

## Установка

```bash
pip install -r requirements.txt
```

Linux: дополнительно `sudo apt install python3-tk libportaudio2`.

## Запуск

```bash
python player.py "путь/к/песне.mp3"
```

или просто `python player.py` и кнопка «Открыть…».

## Управление

- Ползунок скорости 0.5x – 2.0x, пресеты Slowed / Normal / Sped up / Nightcore
- **Пробел** — play/pause
- **← / →** — скорость ±0.05
- **0** — сброс скорости на 1.0

Форматы: mp3, wav, flac, ogg, aiff.
