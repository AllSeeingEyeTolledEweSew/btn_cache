import abc


class Daemon(abc.ABC):
    @abc.abstractmethod
    def terminate(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def run(self) -> None:
        raise NotImplementedError
