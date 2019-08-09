from __future__ import division
from pyproj import Proj, transform, Geod
import webbrowser
import omf, json, warnings, networkx as nx, matplotlib, numpy as np, os, shutil, math, requests, tempfile, random
from matplotlib import pyplot as plt
from omf.feeder import _obToCol
from scipy.spatial import ConvexHull
from os.path import join as pJoin
from sklearn.cluster import KMeans
from flask import Flask, send_file, render_template
from collections import defaultdict

def treeToNxGraph(inTree):
	''' Convert feeder tree to networkx graph. '''
	outGraph = nx.Graph()
	for key in inTree:
		item = inTree[key]
		if 'name' in item.keys():
			if 'parent' in item.keys():
				outGraph.add_edge(item['name'],item['parent'], attr_dict={'type':'parentChild'})
				outGraph.node[item['name']]['type']=item['object']
				if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
				# Note that attached houses via gridEdit.html won't have lat/lon values, so this try is a workaround.
				try: outGraph.node[item['name']]['pos']=(float(item.get('latitude',0)),float(item.get('longitude',0)))
				except: outGraph.node[item['name']]['pos']=(0.0,0.0)
			elif 'from' in item.keys():
				outGraph.add_edge(item['from'],item['to'],attr_dict={'name':item.get('name',''),'type':item['object']})
			elif item['name'] in outGraph:
				# Edge already led to node's addition, so just set the attributes:
				outGraph.node[item['name']]['type']=item['object']
				if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
			else:
				outGraph.add_node(item['name'],attr_dict={'type':item['object']})
				if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
			if 'latitude' in item.keys() and 'longitude' in item.keys():
				try: outGraph.node.get(item['name'],{})['pos']=(float(item['latitude']),float(item['longitude']))
				except: outGraph.node.get(item['name'],{})['pos']=(0.0,0.0)
	return outGraph

def treeToCommsDiNxGraph(inTree):
	''' Convert feeder tree to a DIRECTED networkx graph. '''
	outGraph = nx.DiGraph()
	network_objects = ['regulator', 'overhead_line', 'underground_line', 'transformer', 'fuse', 'switch', 'triplex_line', 'node', 'triplex_node', 'meter', 'triplex_meter', 'load', 'triplex_load', 'series_reactor']
	for key in inTree:
		item = inTree[key]
		if 'object' in item:
			if item['object'] == 'switch':
				if 'OPEN' in item.values(): #super hacky
					continue
		if 'name' in item.keys():#sometimes network objects aren't named!
			if 'parent' in item.keys():
				outGraph.add_edge(item['parent'], item['name'], attr_dict={'type':'parentChild', 'length': 0})#jfk. swapped from,to
				outGraph.node[item['name']]['type']=item['object']
				outGraph.node[item['name']]['pos']=(float(item.get('latitude',0)),float(item.get('longitude',0)))
				if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
			elif 'from' in item.keys():
				outGraph.add_edge(item['from'],item['to'],attr_dict={'type':item['object'], 'length': float(item.get('length',0))})
				if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
			elif item['name'] in outGraph:
				# Edge already led to node's addition, so just set the attributes:
				outGraph.node[item['name']]['type']=item['object']
				if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
			else:
				outGraph.add_node(item['name'],attr_dict={'type':item['object']})
			if 'latitude' in item.keys() and 'longitude' in item.keys():
				outGraph.node.get(item['name'],{})['pos']=(float(item['latitude']),float(item['longitude']))
			if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
		elif 'object' in item.keys() and item['object'] in network_objects:#when name doesn't exist
			if 'from' in item.keys():
				outGraph.add_edge(item['from'],item['to'],attr_dict={'type':item['object'], 'length': float(item.get('length',0))})
			if 'latitude' in item.keys() and 'longitude' in item.keys():
				outGraph.node.get(item['name'],{})['pos']=(float(item['latitude']),float(item['longitude']))
			if 'bustype' in item.keys():
					outGraph.node[item['name']]['substation'] = True
	return outGraph

def createGraph(pathToOmdFile):
	'''Create networkxgraph from omd'''
	with open(pathToOmdFile) as inFile:
		tree = json.load(inFile)['tree']
	nxG = treeToCommsDiNxGraph(tree)
	#use conversion for testing other feeders
	nxG = graphValidator(pathToOmdFile, nxG)
	return nxG

def getSubstation(nxG):
	'''Get the substation node from a feeder'''
	return [sub for sub in nx.get_node_attributes(nxG, 'substation') if nxG.node[sub].get('substation',False)][0]

def getDistance(nxG, start, end):
	'''Get the distance between two points in a graph'''
	dist = math.sqrt( (nxG.node[end]['pos'][1] - nxG.node[start]['pos'][1])**2 + (nxG.node[end]['pos'][0] - nxG.node[start]['pos'][0])**2 )
	return dist

def findNearestPoint(nxG, smartMeter, rfCollectors):
	'''Find the nearest point for a smartMeter based on a list of rfCollectors'''
	nearest = min(rfCollectors, key = lambda rfCollector: getDistance(nxG, smartMeter, rfCollector))
	return nearest

def setSmartMeters(nxG):
	'''sets all nodes with type meter or triplex meter to smartMeters''' 
	#to do add in other areas where needed
	for meter in nx.get_node_attributes(nxG, 'type'):
		if nxG.node[meter]['type'] in ['meter', 'triplex_meter']:
			nxG.node[meter]['smartMeter'] = True

def setSmartMeterBandwidth(nxG, packetSize=10):
	'''sets all smartMeter bandwidth and capacity to the packetSize'''
	for meter in nx.get_node_attributes(nxG, 'smartMeter'):
		if nxG.node[meter].get('smartMeter', False):
			nxG.node[meter]['bandwidthCapacity'] = packetSize
			nxG.node[meter]['bandwidthUse'] = packetSize

def setRFCollectors(nxG, edgeType='switch'):
	'''Set fiber on path between certain edgeType (switch is the defualt for now) and the substation'''
	node_positions = {nodewithPosition: nxG.node[nodewithPosition]['pos'] for nodewithPosition in nx.get_node_attributes(nxG, 'pos')}
	edge_types = {edge: nxG[edge[0]][edge[1]]['type'] for edge in nx.get_edge_attributes(nxG, 'type')}
	substation = getSubstation(nxG)
	#collector position will validate that only one collector is added at each position
	collector_positions = []
	for node in node_positions:
		for edge in nxG.out_edges(node):
			if edge_types.get(edge,'') == edgeType:
				if nxG.node[node]['pos'] not in collector_positions:
					collector_positions.append(nxG.node[node]['pos']) 
					nxG.node[node]['rfCollector'] = True

def setRFCollectorCapacity(nxG, rfBandwidthCap=1000):
	for rfCollector  in nx.get_node_attributes(nxG, 'rfCollector'):
		if nxG.node[rfCollector].get('rfCollector',False):
			nxG.node[rfCollector]['bandwidthCapacity'] = rfBandwidthCap

def setFiber(nxG):
	'''Set fiber between rfcollectors and substation'''
	substation = getSubstation(nxG)
	for rfCollector in nx.get_node_attributes(nxG, 'rfCollector'):
		if nxG.node[rfCollector].get('rfCollector',False):
			shortestPath = nx.bidirectional_shortest_path(nxG, substation, rfCollector)
			for i in range(len(shortestPath)-1):
				nxG[shortestPath[i]][shortestPath[i+1]]['fiber'] = True
				nxG[shortestPath[i]][shortestPath[i+1]]['bandwidthUse'] = 0

def setFiberCapacity(nxG, fiberBandwidthCap=1000000, setSubstationBandwidth=False):
	'''Set fiber capacity'''
	substation = getSubstation(nxG)
	#fiber capacity for line is stored in the substation bansdwidth capacity
	if setSubstationBandwidth:
		nxG.node[substation]['bandwidthCapacity'] = fiberBandwidthCap
	fiberCapacity = nxG.node[substation].get('bandwidthCapacity',0)
	for edge in nx.get_edge_attributes(nxG, 'fiber'):
		if nxG[edge[0]][edge[1]].get('fiber',False): 
			nxG[edge[0]][edge[1]]['bandwidthCapacity'] = fiberCapacity
			#print(nxG[edge[0]][edge[1]])

def setRF(nxG):
	'''Add rf edges between smartMeters and the nearest rfCollector'''
	rfCollectors = [rfCollector for rfCollector  in nx.get_node_attributes(nxG, 'rfCollector') if nxG.node[rfCollector].get('rfCollector', False)]
	for smartMeter in nx.get_node_attributes(nxG, 'smartMeter'):
		if nxG.node[smartMeter].get('smartMeter',False):
			rfCollector = findNearestPoint(nxG, smartMeter, rfCollectors)
			nxG.add_edge(rfCollector, smartMeter,attr_dict={'rf': True, 'type': 'rf'})

def setRFEdgeCapacity(nxG):
	'''Calculate bandwidth use for all rf collectors and '''
	rfCollectors = [rfCollector for rfCollector  in nx.get_node_attributes(nxG, 'rfCollector') if nxG.node[rfCollector].get('rfCollector', False)]
	for rfCollector in rfCollectors:
		#reset bandwidth calculation to 0
		nxG.node[rfCollector]['bandwidthUse'] = 0
		#calculate bandwidth use of each rf connection edge between rfcollectors and smartMeters
		if len(nxG.successors(rfCollector)) > 0:
			splitCapacity = nxG.node[rfCollector].get('bandwidthCapacity',0) / len(nxG.successors(rfCollector))
			for smartMeter in nxG.successors(rfCollector):
				if nxG[rfCollector][smartMeter].get('rf',False):
					nxG[rfCollector][smartMeter]['bandwidthCapacity'] = splitCapacity

def calcEdgeLengths(nxG):
	'''Calculate the lengths of edges based on lat/lon position'''
	G = Geod(ellps='WGS84')
	for edge in nx.edges(nxG):
		nxG[edge[0]][edge[1]]['length'] = G.inv(nxG.node[edge[0]]['pos'][1], nxG.node[edge[0]]['pos'][0], nxG.node[edge[1]]['pos'][1], nxG.node[edge[1]]['pos'][0])[2]

def getFiberCost(nxG, fiberCostPerMeter):
	'''Calculate the cost of fiber'''
	fiber_cost = sum((nxG[edge[0]][edge[1]]['length'])*fiberCostPerMeter for edge in nx.get_edge_attributes(nxG, 'fiber') if nxG[edge[0]][edge[1]].get('fiber',False))
	return fiber_cost

def getrfCollectorsCost(nxG, rfCollectorCost):
	'''Calculate the cost of RF rfCollector equipment'''
	rfCollector_cost = len([rfCollector for rfCollector  in nx.get_node_attributes(nxG, 'rfCollector') if nxG.node[rfCollector].get('rfCollector',False)])*rfCollectorCost
	return rfCollector_cost

def getsmartMetersCost(nxG, smartMeterCost):
	'''Calculate the cost of RF smartMeter equipment'''
	smartMeter_cost = len([smartMeter for smartMeter in nx.get_node_attributes(nxG, 'smartMeter') if nxG.node[smartMeter].get('smartMeter',False)])*smartMeterCost
	return smartMeter_cost

def calcBandwidth(nxG):
	#go through rfCollectors and smartMeters
	#adust later to accept different lengh of rfCollectors
	substation = getSubstation(nxG)
	nxG.node[substation]['bandwidthUse'] = 0
	rfCollectors = [rfCollector for rfCollector in nx.get_node_attributes(nxG, 'rfCollector') if nxG.node[rfCollector].get('rfCollector',False)]
	for rfCollector in rfCollectors:
		#reset bandwidth calculation to 0
		nxG.node[rfCollector]['bandwidthUse'] = 0
		#calculate bandwidth use of each rf connection edge between rfcollectors and smartMeters
		for smartMeter in nxG.successors(rfCollector):
			if nxG[rfCollector][smartMeter].get('rf',False):
				nxG[rfCollector][smartMeter]['bandwidthUse'] = nxG.node[smartMeter]['bandwidthUse']
				nxG.node[rfCollector]['bandwidthUse'] += nxG.node[smartMeter]['bandwidthUse']
		shortestPath = nx.bidirectional_shortest_path(nxG, substation, rfCollector)
		for i in range(len(shortestPath)-1):
			nxG[shortestPath[i]][shortestPath[i+1]]['bandwidthUse'] += nxG.node[rfCollector]['bandwidthUse']
		nxG.node[substation]['bandwidthUse'] += nxG.node[rfCollector]['bandwidthUse']

def clearRFEdges(nxG):
	'''Delete all rf edges'''
	nxG.remove_edges_from(edge for edge in nx.get_edge_attributes(nxG, 'rf') if nxG[edge[0]][edge[1]].get('rf', False))

def clearFiber(nxG):
	'''Set all fiber to false in prep for recalculation'''
	for edge in nx.edges(nxG):
		if nxG[edge[0]][edge[1]].get('fiber',False):
			nxG[edge[0]][edge[1]]['fiber'] = False

def graphGeoJson(nxG):
	'''Create geojson dict for omc file type for communications network'''
	geoJsonDict = {
	"type": "FeatureCollection",
	"features": []
	}
	node_positions = {nodewithPosition: nxG.node[nodewithPosition]['pos'] for nodewithPosition  in nx.get_node_attributes(nxG, 'pos')}
	node_types = {nodewithType: nxG.node[nodewithType]['type'] for nodewithType in nx.get_node_attributes(nxG, 'type')}
	for node in node_positions:
		geoJsonDict['features'].append({
			"type": "Feature", 
			"geometry":{
				"type": "Point",
				"coordinates": [node_positions[node][1], node_positions[node][0]]
			},
			"properties":{
				"name": node,
				"pointType": node_types.get(node,''),
				"substation": nxG.node[node].get('substation',False),
				"rfCollector": nxG.node[node].get('rfCollector',False),
				"smartMeter": nxG.node[node].get('smartMeter',False),
				"bandwidthUse": nxG.node[node].get('bandwidthUse',''),
				"bandwidthCapacity": nxG.node[node].get('bandwidthCapacity','')

			}
		})
	#Add edges to geoJSON
	edge_types = {edge: nxG[edge[0]][edge[1]]['type'] for edge in nx.get_edge_attributes(nxG, 'type')}
	for edge in nx.edges(nxG):
		geoJsonDict['features'].append({
			"type": "Feature", 
			"geometry":{
				"type": "LineString",
				"coordinates": [[node_positions[edge[0]][1], node_positions[edge[0]][0]],[node_positions[edge[1]][1], node_positions[edge[1]][0]]]
			},
			"properties":{
				"edgeType": edge_types.get(edge,''),
				"fiber": nxG[edge[0]][edge[1]].get('fiber',False),
				"rf": nxG[edge[0]][edge[1]].get('rf',False),
				"bandwidthUse": nxG[edge[0]][edge[1]].get('bandwidthUse',''),
				"bandwidthCapacity": nxG[edge[0]][edge[1]].get('bandwidthCapacity',''),
				"source": edge[0],
				"target": edge[1]
			}
		})
	return geoJsonDict

def omcToNxg(omc, fromFile=False):
	'''Convert omc to networkx graph to run calculations. Used when graph is edited in the commsNetViz.html template'''
	outGraph = nx.DiGraph()
	if fromFile:
		with open(omc) as omcFile:
			geoJsonDict = json.load(omcFile)
	else:
		geoJsonDict = omc
	geoNodes = list(filter(lambda x: x['geometry']['type'] == 'Point', geoJsonDict['features']))
	geoEdges = list(filter(lambda x: x['geometry']['type'] == 'LineString', geoJsonDict['features']))
	for node in geoNodes:
		node_attrs = {
			'substation': node['properties']['substation'],
			'smartMeter': node['properties']['smartMeter'],
			'rfCollector': node['properties']['rfCollector'],
			'type': node['properties']['pointType'],
			'bandwidthCapacity': node['properties']['bandwidthCapacity'],
			'bandwidthUse': node['properties']['bandwidthUse'],
			'pos': tuple(reversed(node['geometry']['coordinates']))
		}
		outGraph.add_node(node['properties']['name'],attr_dict=node_attrs)
	for edge in geoEdges:
		edge_attrs = {
			'type': edge['properties']['edgeType'],
			'fiber': edge['properties']['fiber'],
			'rf': edge['properties']['rf'],
			'bandwidthUse': edge['properties']['bandwidthUse'],
			'bandwidthCapacity': edge['properties']['bandwidthCapacity']
		}
		outGraph.add_edge(edge['properties']['source'],edge['properties']['target'],attr_dict=edge_attrs)
	return outGraph

def showOnMap(geoJson):
	'''Open a browser to display a geoJSON object on a map.'''
	tempDir = tempfile.mkdtemp()
	shutil.copy('templates/commsNetViz.html', tempDir)
	with open(pJoin(tempDir,'commsGeoJson.js'),"w") as outFile:
		outFile.write("var geojson =")
		json.dump(geoJson, outFile, indent=4)
	webbrowser.open('file://' + pJoin(tempDir,'commsNetViz.html'))

def saveOmc(geoJson, outputPath, fileName=None):
	'''Save comms geojson dict as .omc proprietary format (it is a geojson)'''
	if fileName is None:
		fileName = 'commsGeoJson'
	if not os.path.exists(outputPath):
		os.makedirs(outputPath)
	with open(pJoin(outputPath, fileName + '.omc'),"w") as outFile:
		json.dump(geoJson, outFile, indent=4)

def graphValidator(pathToOmdFile, inGraph):
	'''If the nodes/edges positions are not in the tree, the spurces and targets in the links key of the omd.json are used. '''
	try:
		node_positions = {nodewithPosition: inGraph.node[nodewithPosition]['pos'] for nodewithPosition  in nx.get_node_attributes(inGraph, 'pos')}
		for edge in nx.edges(inGraph):
			validator = (node_positions[edge[0]] or nxG.node[edge[1]])
	except KeyError:
		raise Exception('Network coordinates are not in the .omd tree. Use the anonymize menu in the circuit editor to generate a circuit with valid coordinates.')
		'''
		This code creates random positional information
		try:
			nxG = latLonValidation(convertOmd(pathToOmdFile))
		except ValueError:
			#No nodes have positions, so create random ones
			nxG = inGraph
			for nodeToChange in nxG.node:
				nxG.node[nodeToChange]['pos'] = (random.uniform(36.9900, 38.8700), random.uniform(-102.0500,-94.5900))
		return nxG'''
	#should invalid lat/lons be included
	nxG = latLonValidation(inGraph)
	return nxG

'''
def convertOmd(pathToOmdFile):
	with open(pathToOmdFile) as inFile:
		omdFile = json.load(inFile)
		links = omdFile['links']
		tree = omdFile['tree']
	nxG = nx.DiGraph()
	objectNodes = set()
	for key in links:
		objectNodes.add(key['objectType'])
		#Account for nodes that are missing names when creating edges
		try:
			sourceEdge = key['source']['name']
		except KeyError:
			try:
				sourceEdge = str(key['target']['name']) + ' Source'
			except KeyError:
				sourceEdge = str(key['source']) + ' Missing Name'
		try:
			targetEdge = key['target']['name']
		except KeyError:
			try:
				targetEdge = key['target']['name'] + ' Target'
			except KeyError:
				targetEdge = str(key['target']) + ' Missing Name'

		#nxG.add_edge(key['source']['name'], key['target']['name'])
		nxG.add_edge(sourceEdge, targetEdge)
		nxG.node[sourceEdge]['pos'] = (float(key['source']['y']), float(key['source']['x']))
		nxG.node[targetEdge]['pos'] = (float(key['target']['y']), float(key['target']['x']))
		sourceTreeIndex = str(key['source']['treeIndex'])
		targetTreeIndex = str(key['target']['treeIndex'])
		try:
			nxG.node[sourceEdge]['type'] = tree[sourceTreeIndex]['object']
			nxG.node[sourceEdge]['name'] = key['source']['name']
			#Get substation - fix later because this seems a little hacky
			if tree[sourceTreeIndex]['bustype'] == 'SWING':
				nxG.node[sourceEdge]['substation'] = True
		except KeyError:
			nxG.node[sourceEdge]['type'] = 'Undefined'
		try:
			nxG.node[targetEdge]['type'] = tree[targetTreeIndex]['object']
			nxG.node[targetEdge]['name'] = key['target']['name']
		except KeyError:
			nxG.node[targetEdge]['type'] = 'Undefined'
	for nodeToChange in nx.get_node_attributes(nxG, 'pos'):
		#nxG.node[nodeToChange]['pos'] = (latitudeCenter + nxG.node[nodeToChange]['pos'][1]/latitude_max, longitudeCenter 
		#								+ nxG.node[nodeToChange]['pos'][0]/longitude_max)
		#print(nxG.node[nodeToChange]['pos'][1], nxG.node[nodeToChange]['pos'][0])
		#print(statePlaneToLatLon(nxG.node[nodeToChange]['pos'][1], nxG.node[nodeToChange]['pos'][0]))
		nxG.node[nodeToChange]['pos'] = statePlaneToLatLon(nxG.node[nodeToChange]['pos'][1], nxG.node[nodeToChange]['pos'][0])
	#print(nxG.node)
	return nxG'''

def latLonValidation(inGraph):
	'''Checks if an omd has invalid latlons, and if so, converts to stateplane coordinates or generates random values '''
	latitude_min = min([inGraph.node[nodewithPosition]['pos'][1] for nodewithPosition  in nx.get_node_attributes(inGraph, 'pos')])
	longitude_min = min([inGraph.node[nodewithPosition]['pos'][0] for nodewithPosition  in nx.get_node_attributes(inGraph, 'pos')])
	latitude_max = max([inGraph.node[nodewithPosition]['pos'][1] for nodewithPosition  in nx.get_node_attributes(inGraph, 'pos')])
	longitude_max = max([inGraph.node[nodewithPosition]['pos'][0] for nodewithPosition  in nx.get_node_attributes(inGraph, 'pos')])
	if latitude_min < -180 or latitude_max > 180 or longitude_min < -90 or longitude_max > 90:
		for nodeToChange in nx.get_node_attributes(inGraph, 'pos'):
			inGraph.node[nodeToChange]['pos'] = statePlaneToLatLon(inGraph.node[nodeToChange]['pos'][1], inGraph.node[nodeToChange]['pos'][0])
	return inGraph

def statePlaneToLatLon(easting, northing, epsg = None):
	if not epsg:
		# Center of the USA default
		epsg = 26978
	inProj = Proj(init = 'EPSG:' + str(epsg), preserve_units = True)
	outProj = Proj(init = 'EPSG:4326')
	lon, lat = transform(inProj, outProj, easting, northing)
	return (lat, lon)

def _tests():
	#setup a comms network, run calculations and display
	nxG = createGraph('static/publicFeeders/Olin Barre LatLon.omd')
	setSmartMeters(nxG)
	setRFCollectors(nxG)
	setFiber(nxG)
	setRF(nxG)
	setSmartMeterBandwidth(nxG)
	setRFCollectorCapacity(nxG)
	setFiberCapacity(nxG, setSubstationBandwidth=True)
	setRFEdgeCapacity(nxG)
	calcBandwidth(nxG)
	print('cost of rf rfCollector equipment: ' + str(getrfCollectorsCost(nxG, 10000)))
	print('cost of rf smartMeter equipment: ' + str(getsmartMetersCost(nxG, 100)))
	print('cost of fiber: ' + str(getFiberCost(nxG, 4)))
	saveOmc(graphGeoJson(nxG), 'output')
	showOnMap(graphGeoJson(nxG))

	#load an omc, recalculate (as if refresh), redisplay
	newNxg = omcToNxg('output/commsGeoJson.omc', fromFile=True)
	clearFiber(newNxg)
	clearRFEdges(newNxg)
	setFiber(newNxg)
	setRF(newNxg)
	setFiberCapacity(newNxg)
	setRFEdgeCapacity(newNxg)
	calcBandwidth(newNxg)
	showOnMap(graphGeoJson(newNxg))

if __name__ == '__main__':
	_tests()