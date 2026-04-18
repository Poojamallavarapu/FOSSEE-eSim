# =========================================================================
#          FILE: Application.py
#
#         USAGE: ---
# hi
#   DESCRIPTION: This main file use to start the Application
#
#       OPTIONS: ---
#  REQUIREMENTS: ---
#          BUGS: ---
#         NOTES: ---
#        AUTHOR: Fahim Khan, fahim.elex@gmail.com
#    MAINTAINED: Rahul Paknikar, rahulp@iitb.ac.in
#                Sumanto Kar, sumantokar@iitb.ac.in
#                Pranav P, pranavsdreams@gmail.com
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: Tuesday 24 February 2015
#      REVISION: Wednesday 07 June 2023
# =========================================================================

import os
import sys
import traceback

if os.name == 'nt':
    from frontEnd import pathmagic  # noqa:F401
    init_path = ''
else:
    import pathmagic    # noqa:F401
    init_path = '../../'

from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.Qt import QSize
from configuration.Appconfig import Appconfig
from frontEnd import ProjectExplorer
from frontEnd import Workspace
from frontEnd import DockArea
from projManagement.openProject import OpenProjectInfo
from projManagement.newProject import NewProjectInfo
from projManagement.Kicad import Kicad
from projManagement.Validation import Validation
from projManagement import Worker
from frontEnd.Chatbot import ChatbotGUI
from PyQt5.QtCore import QTimer


class Application(QtWidgets.QMainWindow):
    """This class initializes all objects used in this file."""
    global project_name
    simulationEndSignal = QtCore.pyqtSignal(QtCore.QProcess.ExitStatus, int)
    errorDetectedSignal = QtCore.pyqtSignal(str)

    def __init__(self, *args):
        """Initialize main Application window."""
        QtWidgets.QMainWindow.__init__(self, *args)

        self.simulationEndSignal.connect(self.plotSimulationData)
        self.errorDetectedSignal.connect(self.handleError)

        self.obj_workspace = Workspace.Workspace()
        self.obj_Mainview = MainView()
        self.obj_kicad = Kicad(self.obj_Mainview.obj_dockarea)
        self.obj_appconfig = Appconfig()
        self.obj_validation = Validation()

        # ── Create chatbot window ──────────────────────────────────────
        self.chatbot_window = ChatbotGUI()

        self.setCentralWidget(self.obj_Mainview)
        self.initToolBar()
        self.initchatbot()

        self.setGeometry(
            self.obj_appconfig._app_xpos,
            self.obj_appconfig._app_ypos,
            self.obj_appconfig._app_width,
            self.obj_appconfig._app_heigth
        )
        self.setWindowTitle(
            self.obj_appconfig._APPLICATION + "-" + self.obj_appconfig._VERSION
        )
        self.showMaximized()
        self.setWindowIcon(QtGui.QIcon(init_path + 'images/logo.png'))

        self.systemTrayIcon = QtWidgets.QSystemTrayIcon(self)
        self.systemTrayIcon.setIcon(QtGui.QIcon(init_path + 'images/logo.png'))
        self.systemTrayIcon.setVisible(True)

    def initchatbot(self):
        """
        Initializes the ChatbotIcon and embeds the ChatbotGUI
        as a dockable panel on the right side of the main window.
        Clicking the icon toggles the panel open/closed.
        """
        # ── Dock widget ────────────────────────────────────────────────
        self.chatbot_dock = QtWidgets.QDockWidget("🤖 eSim AI Assistant", self)
        self.chatbot_dock.setWidget(self.chatbot_window)
        self.chatbot_dock.setMinimumWidth(420)
        self.chatbot_dock.setMinimumHeight(500)
        self.chatbot_dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable |
            QtWidgets.QDockWidget.DockWidgetMovable |
            QtWidgets.QDockWidget.DockWidgetFloatable
        )
        self.chatbot_dock.setStyleSheet("""
            QDockWidget {
                font-weight: bold;
                font-size: 13px;
                color: #0055a5;
            }
            QDockWidget::title {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #e8f4fd, stop:1 #d0e8f8
                );
                padding: 6px 10px;
                border-bottom: 2px solid #0078d4;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
        """)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.chatbot_dock)
        self.chatbot_dock.hide()  # Hidden by default

        self.chatbot_dock.visibilityChanged.connect(
            lambda _: self._reposition_chatbot_icon()
        )

        # ── Floating robot icon button (bottom-right corner) ───────────
        self.chatboticon = QtWidgets.QPushButton(
            self, icon=QtGui.QIcon(init_path + 'images/chatbot.png')
        )
        self.chatboticon.setIconSize(QtCore.QSize(30, 30))
        self.chatboticon.setToolTip("Toggle AI Assistant")
        self.chatboticon.setStyleSheet("""
            QPushButton {
                border-radius: 26px;
                background-color: transparent;
                border: none;
            }
            QPushButton:hover {
                background-color: rgba(0, 120, 212, 0.12);
                border: 2px solid #0078d4;
            }
            QPushButton:pressed {
                background-color: rgba(0, 63, 110, 0.18);
                border: 2px solid #003f6e;
            }
        """)
        self.chatboticon.setFixedSize(52, 52)
        self.chatboticon.clicked.connect(self.openChatbot)

    def openChatbot(self):
        """Toggle the chatbot dock panel open or closed."""
        if self.chatbot_dock.isVisible():
            self.chatbot_dock.hide()
        else:
            # Float the dock so it appears as a separate window
            self.chatbot_dock.setFloating(True)
            self.chatbot_dock.resize(780, 660)
            self.chatbot_dock.show()
            self.chatbot_dock.raise_()

            # ── FIX: use input_field (your Chatbot.py field name) ──────
            if hasattr(self.chatbot_window, 'input_field'):
                self.chatbot_window.input_field.setEnabled(True)
                self.chatbot_window.input_field.setFocus()

            self.obj_appconfig.print_info('Chat Bot function is called')

        self._reposition_chatbot_icon()

    def _reposition_chatbot_icon(self):
        """Keep the icon in the bottom-right corner of the visible area."""
        margin = 12
        btn_w = self.chatboticon.width()
        btn_h = self.chatboticon.height()
        bottom_y = self.height() - btn_h - margin

        if self.chatbot_dock.isVisible() and not self.chatbot_dock.isFloating():
            dock_w = self.chatbot_dock.width()
            x = self.width() - dock_w - btn_w - margin
        else:
            x = self.width() - btn_w - margin

        self.chatboticon.move(x, bottom_y)
        self.chatboticon.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_chatbot_icon()

    def initToolBar(self):
        """Initializes Top and Left Tool Bars."""
        # Top Tool bar
        self.newproj = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/newProject.png'),
            '<b>New Project</b>', self
        )
        self.newproj.setShortcut('Ctrl+N')
        self.newproj.triggered.connect(self.new_project)

        self.openproj = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/openProject.png'),
            '<b>Open Project</b>', self
        )
        self.openproj.setShortcut('Ctrl+O')
        self.openproj.triggered.connect(self.open_project)

        self.closeproj = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/closeProject.png'),
            '<b>Close Project</b>', self
        )
        self.closeproj.setShortcut('Ctrl+X')
        self.closeproj.triggered.connect(self.close_project)

        self.wrkspce = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/workspace.ico'),
            '<b>Change Workspace</b>', self
        )
        self.wrkspce.setShortcut('Ctrl+W')
        self.wrkspce.triggered.connect(self.change_workspace)

        self.helpfile = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/helpProject.png'),
            '<b>Help</b>', self
        )
        self.helpfile.setShortcut('Ctrl+H')
        self.helpfile.triggered.connect(self.help_project)

        self.topToolbar = self.addToolBar('Top Tool Bar')
        self.topToolbar.addAction(self.newproj)
        self.topToolbar.addAction(self.openproj)
        self.topToolbar.addAction(self.closeproj)
        self.topToolbar.addAction(self.wrkspce)
        self.topToolbar.addAction(self.helpfile)

        self.spacer = QtWidgets.QWidget()
        self.spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding)
        self.topToolbar.addWidget(self.spacer)

        self.logo = QtWidgets.QLabel()
        self.logopic = QtGui.QPixmap(
            os.path.join(os.path.abspath(''), init_path + 'images', 'fosseeLogo.png'))
        self.logopic = self.logopic.scaled(QSize(150, 150), QtCore.Qt.KeepAspectRatio)
        self.logo.setPixmap(self.logopic)
        self.logo.setStyleSheet("padding:0 15px 0 0;")
        self.topToolbar.addWidget(self.logo)

        # Left Tool bar
        self.kicad = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/kicad.png'),
            '<b>Open Schematic</b>', self
        )
        self.kicad.triggered.connect(self.obj_kicad.openSchematic)

        self.conversion = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/ki-ng.png'),
            '<b>Convert KiCad to Ngspice</b>', self
        )
        self.conversion.triggered.connect(self.obj_kicad.openKicadToNgspice)

        self.ngspice = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/ngspice.png'),
            '<b>Simulate</b>', self
        )
        self.ngspice.triggered.connect(self.open_ngspice)

        self.model = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/model.png'),
            '<b>Model Editor</b>', self
        )
        self.model.triggered.connect(self.open_modelEditor)

        self.subcircuit = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/subckt.png'),
            '<b>Subcircuit</b>', self
        )
        self.subcircuit.triggered.connect(self.open_subcircuit)

        self.nghdl = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/nghdl.png'), '<b>NGHDL</b>', self
        )
        self.nghdl.triggered.connect(self.open_nghdl)

        self.makerchip = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/makerchip.png'),
            '<b>Makerchip-NgVeri</b>', self
        )
        self.makerchip.triggered.connect(self.open_makerchip)

        self.omedit = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/omedit.png'),
            '<b>Modelica Converter</b>', self
        )
        self.omedit.triggered.connect(self.open_OMedit)

        self.omoptim = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/omoptim.png'),
            '<b>OM Optimisation</b>', self
        )
        self.omoptim.triggered.connect(self.open_OMoptim)

        self.conToeSim = QtWidgets.QAction(
            QtGui.QIcon(init_path + 'images/icon.png'),
            '<b>Schematics converter</b>', self
        )
        self.conToeSim.triggered.connect(self.open_conToeSim)

        self.lefttoolbar = QtWidgets.QToolBar('Left ToolBar')
        self.addToolBar(QtCore.Qt.LeftToolBarArea, self.lefttoolbar)
        self.lefttoolbar.addAction(self.kicad)
        self.lefttoolbar.addAction(self.conversion)
        self.lefttoolbar.addAction(self.ngspice)
        self.lefttoolbar.addAction(self.model)
        self.lefttoolbar.addAction(self.subcircuit)
        self.lefttoolbar.addAction(self.makerchip)
        self.lefttoolbar.addAction(self.nghdl)
        self.lefttoolbar.addAction(self.omedit)
        self.lefttoolbar.addAction(self.omoptim)
        self.lefttoolbar.addAction(self.conToeSim)
        self.lefttoolbar.setOrientation(QtCore.Qt.Vertical)
        self.lefttoolbar.setIconSize(QSize(40, 40))

    def closeEvent(self, event):
        exit_msg = "Are you sure you want to exit the program?"
        exit_msg += " All unsaved data will be lost."
        reply = QtWidgets.QMessageBox.question(
            self, 'Message', exit_msg,
            QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No
        )

        if reply == QtWidgets.QMessageBox.Yes:
            for proc in self.obj_appconfig.procThread_list:
                try:
                    proc.terminate()
                except BaseException:
                    pass
            try:
                for process_object in self.obj_appconfig.process_obj:
                    try:
                        process_object.close()
                    except BaseException:
                        pass
            except BaseException:
                pass
            try:
                self.project.close()
            except BaseException:
                pass
            if self.chatbot_dock.isVisible():
                self.chatbot_dock.close()
            event.accept()
            self.systemTrayIcon.showMessage('Exit', 'eSim is Closed.')

        elif reply == QtWidgets.QMessageBox.No:
            event.ignore()

    def new_project(self):
        text, ok = QtWidgets.QInputDialog.getText(
            self, 'New Project Info', 'Enter Project Name:'
        )
        updated = False
        if ok:
            self.projname = str(text)
            self.project = NewProjectInfo()
            directory, filelist = self.project.createProject(self.projname)
            if directory and filelist:
                self.obj_Mainview.obj_projectExplorer.addTreeNode(directory, filelist)
                updated = True
        if not updated:
            print("No new project created")
            self.obj_appconfig.print_info('No new project created')
            try:
                self.obj_appconfig.print_info(
                    'Current project is : ' +
                    self.obj_appconfig.current_project["ProjectName"]
                )
            except BaseException:
                pass

    def open_project(self):
        print("Function : Open Project")
        self.project = OpenProjectInfo()
        try:
            directory, filelist = self.project.body()
            self.obj_Mainview.obj_projectExplorer.addTreeNode(directory, filelist)
        except BaseException:
            pass

    def close_project(self):
        print("Function : Close Project")
        current_project = self.obj_appconfig.current_project['ProjectName']
        if current_project is None:
            pass
        else:
            temp = self.obj_appconfig.current_project['ProjectName']
            for pid in self.obj_appconfig.proc_dict[temp]:
                try:
                    os.kill(pid, 9)
                except BaseException:
                    pass
            self.obj_Mainview.obj_dockarea.closeDock()
            self.obj_appconfig.current_project['ProjectName'] = None
            self.systemTrayIcon.showMessage(
                'Close',
                'Current project ' + os.path.basename(current_project) + ' is Closed.'
            )

    def change_workspace(self):
        print("Function : Change Workspace")
        self.obj_workspace.returnWhetherClickedOrNot(self)
        self.hide()
        self.obj_workspace.show()

    def help_project(self):
        print("Function : Help")
        self.obj_appconfig.print_info('Help is called')
        print("Current Project is : ", self.obj_appconfig.current_project)
        self.obj_Mainview.obj_dockarea.usermanual()

    @QtCore.pyqtSlot(QtCore.QProcess.ExitStatus, int)
    def plotSimulationData(self, exitCode, exitStatus):
        self.ngspice.setEnabled(True)
        self.conversion.setEnabled(True)
        self.closeproj.setEnabled(True)
        self.wrkspce.setEnabled(True)

        if exitStatus == QtCore.QProcess.NormalExit and exitCode == 0:
            try:
                self.obj_Mainview.obj_dockarea.plottingEditor()
            except Exception as e:
                self.msg = QtWidgets.QErrorMessage()
                self.msg.setModal(True)
                self.msg.setWindowTitle("Error Message")
                self.msg.showMessage('Data could not be plotted. Please try again.')
                self.msg.exec_()
                print("Exception Message:", str(e), traceback.format_exc())
                self.obj_appconfig.print_error('Exception Message : ' + str(e))
                self.errorDetectedSignal.emit("Simulation failed.")

    def handleError(self):
        self.projDir = self.obj_appconfig.current_project["ProjectName"]
        self.output_file = os.path.join(self.projDir, "ngspice_error.log")
        if not self.chatbot_dock.isVisible():
            self.chatbot_dock.setFloating(True)
            self.chatbot_dock.resize(780, 660)
            self.chatbot_dock.show()
        self.delayed_function_call()

    def delayed_function_call(self):
        QTimer.singleShot(2000, lambda: self.chatbot_window.debug_error(self.output_file))

    def open_ngspice(self):
        projDir = self.obj_appconfig.current_project["ProjectName"]
        if projDir is not None:
            projName = os.path.basename(projDir)
            ngspiceNetlist = os.path.join(projDir, projName + ".cir.out")
            if not os.path.isfile(ngspiceNetlist):
                print("Netlist file (*.cir.out) not found.")
                self.msg = QtWidgets.QErrorMessage()
                self.msg.setModal(True)
                self.msg.setWindowTitle("Error Message")
                self.msg.showMessage('Netlist (*.cir.out) not found.')
                self.msg.exec_()
                return
            self.obj_Mainview.obj_dockarea.ngspiceEditor(
                projName, ngspiceNetlist, self.simulationEndSignal, self.chatbot_window)
            self.ngspice.setEnabled(False)
            self.conversion.setEnabled(False)
            self.closeproj.setEnabled(False)
            self.wrkspce.setEnabled(False)
        else:
            self.msg = QtWidgets.QErrorMessage()
            self.msg.setModal(True)
            self.msg.setWindowTitle("Error Message")
            self.msg.showMessage(
                'Please select the project first.'
                ' You can either create new project or open existing project'
            )
            self.msg.exec_()

    def open_subcircuit(self):
        print("Function : Subcircuit editor")
        self.obj_appconfig.print_info('Subcircuit editor is called')
        self.obj_Mainview.obj_dockarea.subcircuiteditor()

    def open_nghdl(self):
        print("Function : NGHDL")
        self.obj_appconfig.print_info('NGHDL is called')
        if self.obj_validation.validateTool('nghdl'):
            self.cmd = 'nghdl -e'
            self.obj_workThread = Worker.WorkerThread(self.cmd)
            self.obj_workThread.start()
        else:
            self.msg = QtWidgets.QErrorMessage()
            self.msg.setModal(True)
            self.msg.setWindowTitle('NGHDL Error')
            self.msg.showMessage('Error while opening NGHDL. Please make sure it is installed')
            self.obj_appconfig.print_error('Error while opening NGHDL. Please make sure it is installed')
            self.msg.exec_()

    def open_makerchip(self):
        print("Function : Makerchip and Verilator to Ngspice Converter")
        self.obj_appconfig.print_info('Makerchip is called')
        self.obj_Mainview.obj_dockarea.makerchip()

    def open_modelEditor(self):
        print("Function : Model editor")
        self.obj_appconfig.print_info('Model editor is called')
        self.obj_Mainview.obj_dockarea.modelEditor()

    def open_OMedit(self):
        self.obj_appconfig.print_info('OMEdit is called')
        self.projDir = self.obj_appconfig.current_project["ProjectName"]
        if self.projDir is not None:
            if self.obj_validation.validateCirOut(self.projDir):
                self.projName = os.path.basename(self.projDir)
                self.obj_Mainview.obj_dockarea.modelicaEditor(self.projDir)
            else:
                self.msg = QtWidgets.QErrorMessage()
                self.msg.setModal(True)
                self.msg.setWindowTitle("Missing Ngspice Netlist")
                self.msg.showMessage(
                    'Current project does not contain any Ngspice file. '
                    'Please create Ngspice file with extension .cir.out'
                )
                self.msg.exec_()
        else:
            self.msg = QtWidgets.QErrorMessage()
            self.msg.setModal(True)
            self.msg.setWindowTitle("Error Message")
            self.msg.showMessage(
                'Please select the project first. You can either '
                'create a new project or open an existing project'
            )
            self.msg.exec_()

    def open_OMoptim(self):
        print("Function : OMOptim")
        self.obj_appconfig.print_info('OMOptim is called')
        if self.obj_validation.validateTool("OMOptim"):
            self.cmd = "OMOptim"
            self.obj_workThread = Worker.WorkerThread(self.cmd)
            self.obj_workThread.start()
        else:
            self.msg = QtWidgets.QMessageBox()
            self.msgContent = (
                "There was an error while opening OMOptim.<br/>"
                "Please make sure OpenModelica is installed in your system.<br/>"
                "To install it on Linux : Go to <a href="
                "https://www.openmodelica.org/download/download-linux"
                ">OpenModelica Linux</a> and install nightly build release.<br/>"
                "To install it on Windows : Go to <a href="
                "https://www.openmodelica.org/download/download-windows"
                ">OpenModelica Windows</a> and install latest version.<br/>"
            )
            self.msg.setTextFormat(QtCore.Qt.RichText)
            self.msg.setText(self.msgContent)
            self.msg.setWindowTitle("Error Message")
            self.obj_appconfig.print_info(self.msgContent)
            self.msg.exec_()

    def open_conToeSim(self):
        print("Function : Schematics converter")
        self.obj_appconfig.print_info('Schematics converter is called')
        self.obj_Mainview.obj_dockarea.eSimConverter()


class MainView(QtWidgets.QWidget):
    def __init__(self, *args):
        QtWidgets.QWidget.__init__(self, *args)

        self.obj_appconfig = Appconfig()
        self.leftSplit = QtWidgets.QSplitter()
        self.middleSplit = QtWidgets.QSplitter()
        self.mainLayout = QtWidgets.QVBoxLayout()
        self.middleContainer = QtWidgets.QWidget()
        self.middleContainerLayout = QtWidgets.QVBoxLayout()

        self.noteArea = QtWidgets.QTextEdit()
        self.noteArea.setReadOnly(True)
        self.obj_appconfig.noteArea['Note'] = self.noteArea
        self.obj_appconfig.noteArea['Note'].append('        eSim Started......')
        self.obj_appconfig.noteArea['Note'].append('Project Selected : None')
        self.obj_appconfig.noteArea['Note'].append('\n')
        self.noteArea.setStyleSheet(
            "QWidget { border-radius: 15px; border: 1px solid gray; padding: 5px; }"
        )

        self.obj_dockarea = DockArea.DockArea()
        self.obj_projectExplorer = ProjectExplorer.ProjectExplorer()

        self.middleSplit.setOrientation(QtCore.Qt.Vertical)
        self.middleSplit.addWidget(self.obj_dockarea)
        self.middleSplit.addWidget(self.noteArea)

        self.middleContainerLayout.addWidget(self.middleSplit)
        self.middleContainer.setLayout(self.middleContainerLayout)

        self.leftSplit.addWidget(self.obj_projectExplorer)
        self.leftSplit.addWidget(self.middleContainer)

        self.mainLayout.addWidget(self.leftSplit)
        self.leftSplit.setSizes([int(self.width() / 4.5), self.height()])
        self.middleSplit.setSizes([self.width(), int(self.height() / 2)])
        self.setLayout(self.mainLayout)


def main(args):
    print("Starting eSim......")
    app = QtWidgets.QApplication(args)
    app.setApplicationName("eSim")

    appView = Application()
    appView.hide()

    splash_pix = QtGui.QPixmap(init_path + 'images/splash_screen_esim.png')
    splash = QtWidgets.QSplashScreen(appView, splash_pix, QtCore.Qt.WindowStaysOnTopHint)
    splash.setMask(splash_pix.mask())
    splash.setDisabled(True)
    splash.show()

    appView.splash = splash
    appView.obj_workspace.returnWhetherClickedOrNot(appView)

    try:
        if os.name == 'nt':
            user_home = os.path.expanduser('~')
        else:
            user_home = os.path.expanduser('~')
        file = open(os.path.join(user_home, ".esim/workspace.txt"), 'r')
        work = int(file.read(1))
        file.close()
    except IOError:
        work = 0

    if work != 0:
        appView.obj_workspace.defaultWorkspace()
    else:
        appView.obj_workspace.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    try:
        main(sys.argv)
    except Exception as err:
        print("Error: ", err)