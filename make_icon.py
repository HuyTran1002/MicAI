from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QPen, QBrush
from PyQt6.QtCore import Qt
import os

def create_app_icon(output_path="app_icon.png"):
    size = 128
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0)) # Trong suốt
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Vẽ nền tròn gradient tím xanh Discord
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#5865F2"))
    painter.drawRoundedRect(8, 8, size - 16, size - 16, 32, 32)
    
    # Vẽ viền neon sáng
    painter.setPen(QPen(QColor("#818CF8"), 4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(8, 8, size - 16, size - 16, 32, 32)
    
    # Vẽ biểu tượng Mic
    # Thân mic
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawRoundedRect(50, 30, 28, 44, 14, 14)
    
    # Vành cung đỡ mic
    pen_arc = QPen(QColor("#FFFFFF"), 5)
    pen_arc.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen_arc)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(40, 44, 48, 40, 0, -180 * 16)
    
    # Chân đế
    painter.drawLine(64, 84, 64, 98)
    painter.drawLine(50, 98, 78, 98)
    
    # Chấm xanh lá báo hiệu On/Live ở góc
    painter.setPen(QPen(QColor("#171923"), 3))
    painter.setBrush(QColor("#10B981"))
    painter.drawEllipse(84, 84, 26, 26)
    
    painter.end()
    pixmap.save(output_path, "PNG")
    print(f"Icon created at {output_path}")

if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    create_app_icon()
