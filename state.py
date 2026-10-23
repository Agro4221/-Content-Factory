import threading

# Поток-безопасный флаг управления ботом. По умолчанию выключен (False)
bot_running = threading.Event()

# Флаги состояния дополнительных подсистем.
behavior_running = threading.Event()
reviews_running = threading.Event()
