# app/core/event_bus.py
import threading
from collections import defaultdict
from queue import Queue, Full
import logging

logger = logging.getLogger("uvicorn")

class EventBus:
    def __init__(self, max_workers: int = 6, queue_size: int = 2000):
        self.subscribers = defaultdict(list)
        self.lock = threading.Lock()
        self.queue = Queue(maxsize=queue_size)
        self.workers = []
        self._start_workers(max_workers)

    def _start_workers(self, max_workers: int):
        for i in range(max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name=f"EventBusWorker-{i+1}"
            )
            t.start()
            self.workers.append(t)

    def _worker_loop(self):
        while True:
            event_type, handler, args, kwargs = self.queue.get()
            try:
                handler(*args, **kwargs)
            except Exception as e:
                logger.error(f"事件处理异常 [{event_type}]: {e}")
            finally:
                self.queue.task_done()

    def subscribe(self, event_type: str, handler):
        with self.lock:
            if handler not in self.subscribers[event_type]:
                self.subscribers[event_type].append(handler)

    def publish(self, event_type: str, *args, **kwargs):
        with self.lock:
            handlers = self.subscribers[event_type][:]
        dropped = 0
        for handler in handlers:
            try:
                self.queue.put_nowait((event_type, handler, args, kwargs))
            except Full:
                dropped += 1
        if dropped:
            logger.warning(f"事件总线队列已满，丢弃事件 [{event_type}] x{dropped}")

# 单例模式，全局复用
bus = EventBus()
