from PyQt5 import QtCore, QtWidgets
import os


class Welcome(QtWidgets.QWidget):
    """
    It contains class responsible for content of dock area part of initial esim Window.
    It creates Welcome page of eSim as shown below in image. The library/browser/welcome.html file is used for html content.
    """

    def __init__(self):
        QtWidgets.QWidget.__init__(self)
        self.vlayout = QtWidgets.QVBoxLayout()
        self.browser = QtWidgets.QTextBrowser()

        _this_file = os.path.abspath(__file__)
        _browser_dir = os.path.dirname(_this_file)   # .../src/browser/
        _src_dir = os.path.dirname(_browser_dir)      # .../src/
        _repo_root = os.path.dirname(_src_dir)        # .../esim--chatbot/
        init_path = _repo_root + '/'

        self.browser.setSource(QtCore.QUrl.fromLocalFile(
            init_path + "library/browser/welcome.html")
        )
        self.browser.setOpenExternalLinks(True)
        self.browser.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        self.vlayout.addWidget(self.browser)
        self.setLayout(self.vlayout)
        self.show()
