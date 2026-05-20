from abc import ABC, abstractmethod

class Manager(ABC):
    """
    Abstract base class for managing different projects.
    """

    @abstractmethod
    def build(self, opt_config)->int:
        """
        Build the project with the given flags.
        """
        pass

    @abstractmethod
    def clean(self) -> int:
        """
        Clean the project.
        """
        pass

    @abstractmethod
    def test(self, num_repeat: int = -1)->float:
        """
        Test the project.
        """
        pass
