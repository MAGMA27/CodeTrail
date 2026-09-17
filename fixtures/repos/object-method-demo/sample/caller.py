from .models import Widget as ImportedWidget
from .models import OtherWidget


def resolved() -> None:
    obj = ImportedWidget()
    obj.run()


def unresolved_reassigned() -> None:
    obj = ImportedWidget()
    obj = OtherWidget
    obj.run()
