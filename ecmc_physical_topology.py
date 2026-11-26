"""Render explicit physical port connections; never infer cables from bus order."""
import json
from qt_compat import QtGui, QtWidgets


def validate_topology(data):
    if not isinstance(data,dict) or data.get('version')!=1:
        raise ValueError('Expected topology version 1')
    nodes,links=data.get('nodes'),data.get('links')
    if not isinstance(nodes,list) or not isinstance(links,list) or len(nodes)>256 or len(links)>512:
        raise ValueError('Topology supports at most 256 nodes / 512 links')
    ids=[]
    for node in nodes:
        if not isinstance(node,dict) or not isinstance(node.get('id'),str) or not node['id'] or node['id'] in ids:
            raise ValueError('Node IDs must be unique nonempty strings')
        ids.append(node['id'])
    ports=set()
    for link in links:
        if not isinstance(link,dict):raise ValueError('Invalid link')
        for key,portkey in (('from','from_port'),('to','to_port')):
            node,port=link.get(key),link.get(portkey)
            if node not in ids or type(port) is not int or not 0<=port<=3:
                raise ValueError('Links require known nodes and port numbers 0–3')
            endpoint=(node,port)
            if endpoint in ports:raise ValueError('A physical port cannot have multiple cables')
            ports.add(endpoint)
        if link['from']==link['to']:raise ValueError('Self-links are not supported')
    return nodes,links


class PhysicalTopology(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Physical wiring from an explicit port map. Current discovery exposes slave order, not cable adjacency. Load a verified JSON port map; this view does not claim live wiring verification.')
        note.setWordWrap(True);layout.addWidget(note)
        button=QtWidgets.QPushButton('Load physical port map JSON');button.clicked.connect(self._load);layout.addWidget(button)
        self.status=QtWidgets.QLabel('No physical port map loaded');layout.addWidget(self.status)
        self.scene=QtWidgets.QGraphicsScene(self)
        self.view=QtWidgets.QGraphicsView(self.scene);layout.addWidget(self.view)
    def set_topology(self,data):
        nodes,links=validate_topology(data)
        self.scene.clear()
        positions={node['id']:((i%4)*260,(i//4)*150) for i,node in enumerate(nodes)}
        for link in links:
            x,y=positions[link['from']];u,v=positions[link['to']]
            self.scene.addLine(x+100,y+50,u+100,v+50,QtGui.QPen(QtGui.QColor('#287bc0'),2))
            label=self.scene.addText(f"{link['from']}:{link['from_port']} → {link['to']}:{link['to_port']}")
            label.setPos((x+u)/2,(y+v)/2+70)
        for node in nodes:
            x,y=positions[node['id']]
            self.scene.addRect(x,y,210,60,QtGui.QPen(QtGui.QColor('#287bc0')),QtGui.QBrush(QtGui.QColor('#e8f1f8')))
            label=self.scene.addText(node['id']+'\n'+str(node.get('label','')));label.setPos(x+5,y+5)
        self.status.setText(f'{len(nodes)} nodes · {len(links)} explicit cables · imported map')
    def _load(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Load port map','','JSON (*.json)')
        if path:
            try:
                with open(path,encoding='utf-8') as f:self.set_topology(json.load(f))
            except Exception as ex:self.status.setText(str(ex))
