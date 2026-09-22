import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
    id: root
    moduleName: "justin.session-restore"
    property bool popupOpen: false
    property bool sessionEnabled: true

    function close() { popupOpen = false }

    function toggleSession(value) {
        sessionEnabled = value
        Quickshell.execDetached([Quickshell.env("HOME") + "/.local/bin/omarchy-session", "toggle"])
    }

    function refreshStatus() { statusProcess.running = true }
    Component.onCompleted: refreshStatus()
    onPopupOpenChanged: if (popupOpen) refreshStatus()

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        tooltipText: "Desktop Session · save and restore your workspace"
        iconComponent: Component {
            Item {
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1
                    color: "transparent"
                    border.color: button.foreground
                    border.width: 1.5
                    radius: 2
                    Rectangle {
                        x: 3; y: 3
                        width: parent.width * 0.35
                        height: parent.height - 6
                        color: button.foreground
                        radius: 1
                        opacity: 0.8
                    }
                    Rectangle {
                        x: parent.width * 0.55; y: 3
                        width: parent.width * 0.3
                        height: parent.height - 6
                        color: button.foreground
                        radius: 1
                        opacity: 0.5
                    }
                }
            }
        }
        active: root.popupOpen
        useActiveColor: true
        activeColor: Color.accent
        onPressed: function(mouseButton) {
            if (mouseButton === Qt.LeftButton) root.popupOpen = !root.popupOpen
        }
    }

    PopupCard {
        id: popup
        anchorItem: button
        bar: root.bar
        owner: root
        open: root.popupOpen
        contentWidth: popup.fittedContentWidth(Style.space(360))
        contentHeight: popup.fittedContentHeight(column.implicitHeight)

        Column {
            id: column
            anchors.fill: parent
            spacing: Style.space(12)

            Text {
                text: "Desktop Session"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
            }

            Text {
                width: parent.width
                text: "Automatically saves your open apps and desktop layout before shutdown, restart, or logout, then restores them after login."
                color: Qt.darker(root.bar.foreground, 1.35)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
                wrapMode: Text.WordWrap
            }

            Toggle {
                width: parent.width
                label: "Automatic session restore"
                description: "Save and restore the desktop session"
                checked: root.sessionEnabled
                onClicked: root.toggleSession(!root.sessionEnabled)
            }
        }
    }

    Process {
        id: statusProcess
        command: [Quickshell.env("HOME") + "/.local/bin/omarchy-session", "status"]
        stdout: StdioCollector { id: statusOutput }
        onExited: {
            if (exitCode !== 0) return
            try {
                var settings = JSON.parse(statusOutput.text).settings
                root.sessionEnabled = !!settings.auto_save && !!settings.auto_restore
            } catch (error) {}
        }
    }
}
