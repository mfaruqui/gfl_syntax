#!/usr/bin/env python2.7
"""
FUDG graph data structures and algorithms for reasoning about possible CBB heads 
(internal a.k.a. 'topcandidates' and external a.k.a. 'parentcandidates').

Some things done here but not in the parser:
- ensure only one cbbhead child per CBB
- ensure no token appears in more than one lexical node
- ensure the root is never attached to anything else
- ensure CBBs with identical childsets are merged, without losing cbbhead information

TODO:
- ensure nodes attaching to root can't also attach to something else unless it is a CBB
  (or is this enforced already via the forest-except-CBB-edges constraint?)

- prohibit:
[a b]
c > b

[a b]
(b c)

(a* b c)
(a b* c)

@author: Nathan Schneider (nschneid@cs.cmu.edu)
@since: 2013-02-14
"""
from __future__ import print_function, division
import os, sys, re, itertools


class FixedDict(dict):
    '''Dict subclass which prevents reassignment to existing keys.'''
    
    def __setitem__(self, key, newvalue):
        if key in self:
            raise KeyError('FixedDict cannot reassign to key {0!r} (current: {1!r}, new value: {2!r})'.format(key,self[key],newvalue))
        dict.__setitem__(self, key, newvalue)

# from Naomi's code:
class TreeNode(object):
    def __init__(self, name, children):
        self.name = name
        self.children = set(children)

    def add_child(self, child):
        self.children.add(child)

    def remove_child(self, child):
        self.children.remove(child)

    def deepcopy(self):
        return TreeNode(self.name,
                        [child.deepcopy() for child in self.children])
                        

def reachable(n1,n2):
	if n1 is n2: return True
	for c in n1.children:
		if reachable(c,n2): return True

class FUDGNode(TreeNode):
	def __init__(self, *args, **kwargs):
		TreeNode.__init__(self, *args, **kwargs)
		self.childedges = set()
		self.parentedges = set()
		self.parents = set()
		self.height = 0	# length of longest path from this node to a leaf
		self.depth = -1	# length of longest path from a parentless node to this one
		self.frag = Fragment({self}, {self})
	
	def add_child(self, node, label=None):
		assert self.name!=node.name
		assert not node.isRoot or (self.isCBB and label is not None)
		TreeNode.add_child(self, node)
		self.childedges.add((node, label))
		# check for cycles
		if reachable(node, self):
			raise Exception('Adding {0} as a child of {1} would create a cycle!'.format(node,self))
		
		node.parentedges.add((self, label))
		node.parents.add(self)
		self._setMinHeight(node.height+1)
		
		#print(self,'.add_child',node)
		self.frag |= node.frag	# unify the fragments, updating all references from member nodes
		#self.frag.roots.remove(node)	# no longer a root because it has a parent
		self.frag.roots -= {node}	# TODO

		# recompute depths in the entire fragment
		for n in self.frag.nodes:
			n.depth = -1
		for n in self.frag.roots:
			n._setMinDepth(0)
	
	def remove_child(self, child):
		raise Exception('Not supported')
	
	def _setMinHeight(self, h):
		if self.height<h:
			self.height = h
			for p in self.parents:
				assert p.name!=self.name,(self,self.parents)
				p._setMinHeight(h+1)
	
	def _setMinDepth(self, d):
		if self.depth<d:
			self.depth = d
			#print(self,d,self.children)
			for c in self.children:
				c._setMinDepth(d+1)
	
	def __repr__(self):
		return '<'+self.name.encode('utf-8')+'>'
	
	def descendantsIter(self): return itertools.groupby(itertools.chain(self.children, itertools.imap(FUDGNode.descendantsIter, self.children)))
	@property
	def descendants(self): return set(self.descendantsIter())
	
	def get_yield(self): return {tkn for c in self.children for tkn in c.get_yield()}
	
	@property
	def isRoot(self): return isinstance(self, RootNode)
	@property
	def isCoord(self): return isinstance(self, CoordinationNode)
	@property
	def isLexical(self): return isinstance(self, LexicalNode)
	@property
	def isCBB(self): return isinstance(self, CBBNode)
	@property
	def isFirm(self):
		'''i.e. anything but a CBB (fudge) node'''
		return self.isRoot or self.isLexical or self.isCoord

class LexicalNode(FUDGNode):
	parentcandidates = None
	
	def __init__(self, name, tokens, token2lexnode):
		assert name!='$$'
		FUDGNode.__init__(self, name, [])
		self.tokens = tokens
		for tkn in tokens:
			assert tkn not in token2lexnode,(tkn,token2lexnode)
			token2lexnode[tkn] = self
			
	def get_yield(self):
		return set(self.tokens) | FUDGNode.get_yield(self)
	
	@property
	def json_name(self):	# not guaranteed to be consistent across invocations!
		return ('M' if len(self.tokens)>1 else '') + 'W('+self.name+')'

class RootNode(FUDGNode):
	def __init__(self, children=None):
		FUDGNode.__init__(self, '$$', children or set())
		self.parentcandidates = None
	
	@property
	def json_name(self):
		return 'W($$)'

class CoordinationNode(FUDGNode):
	def __init__(self, name, coords, conjuncts=None, modifiers=None):
		FUDGNode.__init__(self, name, [])	# no TreeNode children, as we won't be traversing CoordinationNodes anyway
		self.coords = coords
		self.conjuncts = conjuncts or set()

class CBBNode(FUDGNode):
	def __init__(self, name, members=None, externalchildren=None, top=None):
		self.top = top
		self.members = members or set()
		self.externalchildren = externalchildren or set()
		self._pointerto = None
		FUDGNode.__init__(self, name, self.members | self.externalchildren)
		self._parentcandidates = None
		self._topcandidates = None

		
	def add_member(self, node, specified_top):
		self.members.add(node)
		if specified_top:
			assert self.top is None,'CBB can only have one specified top: '+repr(self.top)+', '+repr(node)
			self.top = node
		FUDGNode.add_child(self, node, label=('top' if specified_top else 'unspec'))
		
	def add_child(self, node, **kwargs):
		self.externalchildren.add(node)
		FUDGNode.add_child(self, node, **kwargs)
		
	def become_pointer(self, node):
		'''Merge the two CBBs and assume a reference to the other instance'''
		print('MERGE CBB ',self,'into',node, file=sys.stderr)
		assert node.isCBB
		assert node.members==self.members
		assert (node.top is None) or (self.top is None) or node.top==self.top
		if node.top is None: node.top = self.top
		else: self.top = node.top
		node.height = max(self.height,node.height)
		node.depth = max(self.depth,node.depth)
		for c in self.externalchildren:
			node.add_child(c)
		self.externalchildren = node.externalchildren
		self.members = node.members
		self.children = node.children
		self.parents = node.parents
		self.childedges = node.childedges
		self.parentedges = node.parentedges
		self._pointerto = node
		#assert False
		
	@property
	def json_name(self): return self.name if self._pointerto is None else self._pointerto.name
	
	def __getattr__(self, name):
		if name not in ('parentcandidates', 'topcandidates', 'height', 'depth'):
			raise AttributeError(name)
		if self._pointerto is not None:
			#assert getattr(self._pointerto, '_'+name) is not None,self.__dict__
			return getattr(self._pointerto, '_'+name)
		return self.__dict__['_'+name]
	def __setattr__(self, name, val):
		if name in ('parentcandidates', 'topcandidates', 'height', 'depth'):
			if self._pointerto is not None:
				self._pointerto.__setattr__(name, val)
			else:
				FUDGNode.__setattr__(self, '_'+name, val)
		else:
			FUDGNode.__setattr__(self, name, val)
	
class Fragment(object):
	def __init__(self, roots, nodes):
		self.roots = roots
		for root in roots:
			assert root in nodes
		self.nodes = nodes
		
	def __or__(self, that):
		if self is not that:
			# merge 'that' contents in with 'self'
			self.roots |= that.roots
			self.nodes |= that.nodes
			
			# update node references from 'that' to point to 'self'
			for n in that.nodes:
				n.frag = self
			
		return self

	def __repr__(self):
		return '<Fragment@'+str(id(self))+'> '+str(self.roots)+' '+str(self.nodes)
	
class Graph(object):
	def __init__(self, nodes):
		self.nodes = nodes
		
	@property
	def firmNodes(self):
		return {n for n in self.nodes if n.isFirm}

class FUDGGraph(Graph):
	def __init__(self, graphJ):
		self.alltokens = graphJ['tokens']
		self.token2lexnode = {}
		self.lexnodes = set()
		self.cbbnodes = set()
		self.anaphlinks = set()
		self.root = RootNode()
		self.coordnodes = set()
		self.coordnodenames = {}
		self.nodesbyname = FixedDict({'W($$)': self.root})	# GFL name -> node
		
		# lexical nodes
		coordNodes = set()
		cbbmws = set()
		
		'''
		for punct in set('.,!-()$:') | {'....'}:	# TODO: deal with dependency converted input
			graphJ['nodes'][:] = [x for x in graphJ['nodes'] if x in graphJ['node2words'] or x!='W('+punct+')']
		'''
		
		def add_lex(lex, tkns):
			lname = lex[2:-1]
			if isinstance(tkns, basestring):
				n = LexicalNode(lname, {tkns}, self.token2lexnode)
			else:
				n = LexicalNode(lname, set(tkns), self.token2lexnode)
			self.lexnodes.add(n)
			self.nodesbyname[lex] = n
		
		for lex in graphJ['nodes']:
			if lex=='W(,)':
				assert False,('Nodes list in JSON input should probably not contain punctuation:',graphJ['nodes'],graphJ['node2words'])
			if lex=='W($$)':
				continue
			elif lex.startswith('MW('):
				lname = lex[3:-1]
				tokens = set(graphJ['node2words'][lex])
				n = LexicalNode(lname, tokens, self.token2lexnode)
				self.lexnodes.add(n)
				self.nodesbyname[lex] = n
			elif lex.startswith('W('):
				add_lex(lex, graphJ['node2words'][lex])
			elif lex[0]=='$':
				coordNodes.add(lex)
			else:
				assert lex.startswith('CBB')
				members = set()
				if lex.startswith('CBBMW'):	# multiword relaxed to a CBB
					cbbmws.add(lex)
				n = CBBNode(lex)
				self.cbbnodes.add(n)
				self.nodesbyname[lex] = n
				
		# coordination nodes (which depend on lexical nodes)
		for lex in coordNodes:
			coords = set()
			for tkn,lbl in graphJ['extra_node2words'][lex]:
				assert lbl=='Coord'
				if tkn not in self.token2lexnode:	# create lexical node for the coordinator
					coord = LexicalNode(tkn, {tkn}, self.token2lexnode)
					self.lexnodes.add(coord)
					self.token2lexnode[tkn] = coord
				coords.add(self.token2lexnode[tkn])	# handles multiwords, even if annotation erroneously refers to an individual token of a multiword
			n = CoordinationNode(lex, coords=coords)
			self.coordnodes.add(n)
			self.nodesbyname[lex] = n
		
		# CBBMWs (attach lexical node members)
		for cbbmw in cbbmws:
			cbb = self.nodesbyname[cbbmw]
			for lex in graphJ['node2words'][cbbmw]:
				if 'W('+lex+')' not in self.nodesbyname:
					add_lex('W('+lex+')', lex)
				cbb.add_member(self.nodesbyname['W('+lex+')'], False)
			assert cbb.members
		
		self.nodes = {self.root} | self.lexnodes | self.coordnodes | self.cbbnodes
		
		# edges
		for p,c,lbl in graphJ['node_edges']:
			pnode = self.nodesbyname[p]
			assert c in self.nodesbyname,(c,self.nodesbyname)
			cnode = self.nodesbyname[c]
			if lbl=='Conj':
				pnode.conjuncts.add(cnode)
			elif lbl=='unspec':
				pnode.add_member(cnode, specified_top=False)
			elif lbl=='cbbhead':
				pnode.add_member(cnode, specified_top=True)
			elif lbl=='Anaph':
				self.anaphlinks.add((p,c))	# simply store these for the present purposes
			else:
				assert lbl is None,lbl
				pnode.add_child(cnode)
		
		

		
		# merge CBBs with identical member sets
		cbbs = list(sorted(self.cbbnodes, key=lambda n: n.name))
		assert None not in cbbs
		for i in range(len(cbbs)):
			for h in range(i):
				if cbbs[h] and cbbs[h].members==cbbs[i].members:
					cbbs[i].become_pointer(cbbs[h])
					self.cbbnodes.remove(cbbs[i])
					self.nodes.remove(cbbs[i])
					cbbs[i] = None
					break
					
		assert self.lexnodes
		

	@property
	def isProjective(self):
		usedtokens = [tkn for tkn in self.alltokens if tkn in self.token2lexnode and len(self.token2lexnode[tkn].frag.nodes)>1]
		# for each node, determine whether its yield corresponds to a contiguous portion of usedtokens
		for n in self.nodes:
			if n.isRoot or len(n.frag.nodes)<2: continue
			yieldtkns = n.get_yield()
			yieldoffsets = {usedtokens.index(tkn) for tkn in yieldtkns}
			if max(yieldoffsets)-min(yieldoffsets)!=len(yieldoffsets)-1:
				print('nonprojective:',usedtokens,n,yieldoffsets, file=sys.stderr)
				return False
		return True

	def to_json_simplecoord(self):
		outJ = {"tokens": list(self.alltokens), "extra_node2words": {},
				"nodes": [n.json_name for n in self.nodes], 
				"node2words": {n.json_name: list(n.tokens) for n in self.lexnodes},
				# exclude CBBMW member links (these are handled separately)
				"node_edges": [[p.json_name, n.json_name, lbl and lbl.replace('top','cbbhead')] for n in self.nodes for p,lbl in n.parentedges if not p.name.startswith('CBBMW')]}
		if any(self.root.json_name in (x,y) for x,y,l in outJ["node_edges"]):
			outJ["node2words"][self.root.json_name] = ['$$']
		else:
			outJ["nodes"].remove(self.root.json_name)
		for cbb in self.cbbnodes:
			if cbb.name.startswith('CBBMW'):	# hacky
				outJ["node2words"][cbb.name] = [tkn for n in cbb.members for tkn in n.tokens]
		for e in self.anaphlinks:	# TODO
			outJ["node_edges"]
		return outJ
		

def simplify_coord(G):
	'''
	Simplify the graph by removing coordination nodes, choosing one of the coordinators as the head.
	'''
	for n in sorted(G.nodes, key=lambda node: node.height):
		if n.isCoord:
			newhead = next(iter(sorted(n.coords, key=lambda v: v.name)))	# arbitrary but consistent choice
			
			# detach newhead
			#n.children.remove(newhead)
			#n.childedges -= {(c,e) for (c,e) in n.childedges if c is newhead}
			#print(newhead.parents)
			#newhead.parents.remove(n)
			#newhead.parentedges -= {(p,e) for (p,e) in newhead.parentedges if p is n}
			


			
			maxht = float('-inf')
			for c in n.coords | n.conjuncts:
				if c is not newhead:
					# detach c from n
					'''
					assert c not in n.frag.roots
					c.frag = Fragment({c}, {c} | c.descendants)
					for d in c.frag.nodes:
						n.frag.nodes.remove(d)
					'''
					# attach c to newhead
					maxht = max(maxht, c.height)
					#print(c.frag,c.frag.roots,c.frag.nodes)
					#print('BEFORE:',c.frag,n.frag)
					newhead.add_child(c)
					#print('AFTER:',c.frag,n.frag)
					#assert False
					assert c in G.nodes

			for c in n.children:	# modifiers
				# detach c from n
				'''
				assert c not in n.frag.roots
				c.frag = Fragment({c}, {c} | c.descendants)
				for d in c.frag.nodes:
					n.frag.nodes.remove(d)
				'''
				#n.children.remove(c)
				#n.childedges -= {(c,e) for (c,e) in n.childedges if c is c}
				#print(n,c,c.parents)
				c.parents.remove(n)
				c.parentedges -= {(p,e) for (p,e) in c.parentedges if p is n}
				
				# attach c to newhead
				maxht = max(maxht, c.height)
				newhead.add_child(c)
				assert c in G.nodes
				
			assert newhead.height==maxht+1,(n,n.height,newhead,newhead.height,maxht,newhead.children)
			
			if n.parents:
				for p in n.parents:
					p.add_child(newhead)
			else:	# n is headless
				assert n in n.frag.roots
				for v in n.frag.nodes:
					v.depth = -1
				for v in n.frag.roots:
					v._setMinDepth(0)

			try:
				assert newhead.depth==n.depth,(n,n.depth,newhead,newhead.depth)
			except AssertionError as ex:
				print(ex, 'There is a legitimate edge case in which this fails: the coordinator is also a member of a CBB and so will continue to have greater depth than the coordination node even when it replaces it', file=sys.stderr)

			assert newhead in G.nodes
			
			# remove n utterly
			n.frag.nodes.remove(n)
			
	G.nodes -= {n for n in G.nodes if n.isCoord}

def upward(F):
	'''
	For each CBB in the graph or fragment, traverse bottom-up to identify the possible 
	top nodes (internal heads) that might obtain in a full analysis. 
	Result: .topcandidates, not containing any CBB nodes
	'''
	for n in sorted(F.nodes, key=lambda node: node.height):
		assert not n.isCoord	# graph should have been simplified to remove coordination nodes
		if n.isCBB:
			n.topcandidates = set()
			
			# ensure only one cbbhead child per CBB
			assert [e for (c,e) in n.childedges].count('top')<=1,n.childedges
			
			for (c,e) in n.childedges:
				if e=='top':
					n.topcandidates = set(c.topcandidates) if c.isCBB else {c}
					#n.topcandidates = {c}
					#if c.isCBB:
					#	n.topcandidates |= {x for x in c.topcandidates if not c.isCBB}
					break	# there is only one cbbhead
				elif e=='unspec':
					n.topcandidates |= c.topcandidates if c.isCBB else {c}
					#n.topcandidates.add(c)
					#if c.isCBB:
					#	n.topcandidates |= {x for x in c.topcandidates if not c.isCBB}
				else:
					assert e is None
			assert n not in n.topcandidates
			assert not any(1 for x in n.topcandidates if x.isCBB)
			#print(n, 'TOPCANDIDATES', n.topcandidates)

def downward(G):
	'''
	For each lexical node, identify the possible attachments (heads, not CBBs) it might take in some full analysis.
	For each CBB node, identify the possible attachments to non-CBB heads its *top node* (internal head) might take in some full analysis. 
	If the node in question is unattached, that will be all non-CBB nodes that are not its descendants.
	Result: .parentcandidates, not containing any CBB nodes
	'''
	for n in sorted(G.nodes, key=lambda node: node.depth):
		assert not n.isCoord	# graph should have been simplified to remove coordination nodes
		if not n.isRoot:
			n.parentcandidates = G.firmNodes - {n} - n.descendants
			assert n.parentcandidates
			#print(n, n.parentcandidates)
			#print(n, n.parentedges, n.parents, n.parentcandidates)
			for (p,e) in n.parentedges:
				if p.isCBB:
					#print('   CBB topcandidates:',p,p.topcandidates)
					if n in p.members:
						tcands = n.topcandidates if n.isCBB else {n}
						cands = set()
						if p.topcandidates & tcands:	# (top of) n might be the top of the CBB
							assert p.parentcandidates is not None,(n,p,p._pointerto,n.depth,p.depth,n.frag,p.frag)
							cands |= p.parentcandidates
						if p.topcandidates!=tcands:	# (top of) n might not be the top of the CBB
							siblings = p.members - {n}
							for sib in siblings:
								cands |= sib.topcandidates if sib.isCBB else {sib}
						n.parentcandidates &= cands
					else:
						assert n in p.externalchildren	# edge modifies a CBB
						n.parentcandidates &= p.topcandidates	# can be any firm node that might be the top of the CBB
				else:
					assert p.isFirm
					n.parentcandidates &= {p}	# edge attaches to something other than a CBB, so we know it's for real
				#print('  ',n,'   after',(p,e),n.parentcandidates)
			#print()
			#assert n.parentcandidates,(n,(n.topcandidates if n.isCBB else []), n.parents,n.parentedges,[p.topcandidates for p in n.parents if p.isCBB],n.parentcandidates)
			assert n.parentcandidates,'Could not find any possible heads for '+repr(n)+'. Is the annotation valid?' 
def test():
	'''Some rudimentary test cases.'''
	from spanningtrees import spanning
	g1 = {'tokens': ['I', 'think', "I'm", 'a', 'wait', 'an', 'hour', 'or', '2', '&', 'THEN', 'tweet', '@sinittaofficial', '...'], 'node_edges': [['$a', 'W(tweet)', 'Conj'], ['$a', 'W(wait)', 'Conj'], ['$o', 'W(2)', 'Conj'], ['$o', 'W(hour)', 'Conj'], ["W(I'm)", '$a', None], ['W(hour)', 'W(an)', None], ['W(think)', "W(I'm)", None], ['W(think)', 'W(I)', None], ['W(tweet)', 'W(@sinittaofficial)', None], ['W(wait)', '$o', None]], 'nodes': ['$a', '$o', 'MW(&_THEN)', 'W(2)', 'W(@sinittaofficial)', "W(I'm)", 'W(I)', 'W(an)', 'W(hour)', 'W(think)', 'W(tweet)', 'W(wait)'], 'extra_node2words': {'$o': [['or', 'Coord']], '$a': [['&', 'Coord'], ['THEN', 'Coord']]}, 'node2words': {'W(@sinittaofficial)': ['@sinittaofficial'], "W(I'm)": ["I'm"], 'W(2)': ['2'], 'W(think)': ['think'], 'W(tweet)': ['tweet'], 'W(I)': ['I'], 'W(an)': ['an'], 'W(wait)': ['wait'], 'W(hour)': ['hour'], 'MW(&_THEN)': ['THEN', '&']}}
	g2 = {"tokens": ["@mandaffodil", "lol", ",", "we", "are", "one", ".", "and", "that", "was", "me", "this", "weekend", "especially", ".", "maybe", "put", "it", "off", "until", "you", "feel", "like", "~", "talking", "again", "?"], "node_edges": [["CBB1", "W(again)", "unspec"], ["CBB1", "W(feel)", "cbbhead"], ["CBB1", "W(you)", None], ["MW(put_off)", "W(it)", None], ["MW(put_off)", "W(until)", None], ["W($$)", "W(are)", None], ["W($$)", "W(lol)", None], ["W($$)", "W(maybe)", None], ["W($$)", "W(was)", None], ["W(are)", "W(one)", None], ["W(are)", "W(we)", None], ["W(feel)", "W(like)", None], ["W(like)", "W(talking)", None], ["W(maybe)", "MW(put_off)", None], ["W(until)", "CBB1", None], ["W(was)", "W(me)", None], ["W(was)", "W(that)", None], ["W(was)", "W(weekend)", None], ["W(weekend)", "W(especially)", None], ["W(weekend)", "W(this)", None]], "nodes": ["CBB1", "MW(put_off)", "W($$)", "W(again)", "W(are)", "W(especially)", "W(feel)", "W(it)", "W(like)", "W(lol)", "W(maybe)", "W(me)", "W(one)", "W(talking)", "W(that)", "W(this)", "W(until)", "W(was)", "W(we)", "W(weekend)", "W(you)"], "extra_node2words": {}, "node2words": {"W(are)": ["are"], "W(especially)": ["especially"], "W(like)": ["like"], "W(again)": ["again"], "W(that)": ["that"], "W(me)": ["me"], "W($$)": ["$$"], "W(maybe)": ["maybe"], "MW(put_off)": ["put", "off"], "W(was)": ["was"], "W(until)": ["until"], "W(you)": ["you"], "W(we)": ["we"], "W(feel)": ["feel"], "W(it)": ["it"], "W(talking)": ["talking"], "W(this)": ["this"], "W(one)": ["one"], "W(lol)": ["lol"], "W(weekend)": ["weekend"]}}
	g3 = {"tokens": ["A", "Top", "Quality", "Sandwich", "made", "to", "artistic", "standards", "."], "node_edges": [["CBB1", "W(A)", "unspec"], ["CBB1", "W(Quality)", "unspec"], ["CBB1", "W(Sandwich)", "cbbhead"], ["CBB1", "W(Top)", "unspec"], ["CBB2", "W(artistic)", "unspec"], ["CBB2", "W(standards)", "cbbhead"], ["W($$)", "CBB1", None], ["W(Sandwich)", "W(made)", None], ["W(made)", "W(to)", None], ["W(to)", "CBB2", None]], "nodes": ["CBB1", "CBB2", "W($$)", "W(A)", "W(Quality)", "W(Sandwich)", "W(Top)", "W(artistic)", "W(made)", "W(standards)", "W(to)"], "extra_node2words": {}, "node2words": {"W(made)": ["made"], "W(standards)": ["standards"], "W($$)": ["$$"], "W(to)": ["to"], "W(Top)": ["Top"], "W(artistic)": ["artistic"], "W(A)": ["A"], "W(Sandwich)": ["Sandwich"], "W(Quality)": ["Quality"]}}
	g4 = {"tokens": ["I~1", "wish", "I~2", "had", "you~1", "as", "my~1", "dentist~1", "early", "on", "in", "my~2", "life", "-", "maybe", "my~3", "teeth", "would", "have", "been", "a", "lot", "better", "then", "they", "are~1", "now~1", ",", "However", "I~3", "am", "glad", "you~2", "are~2", "my~4", "dentist~2", "now~2", "."], "node_edges": [["CBB1", "CBB2", "unspec"], ["CBB1", "W(as)", "unspec"], ["CBB1", "W(had)", "unspec"], ["CBB2", "W(early)", "cbbhead"], ["CBB2", "W(in)", "unspec"], ["CBB2", "W(life)", "unspec"], ["CBB2", "W(my~2)", "unspec"], ["CBB2", "W(on)", "unspec"], ["W($$)", "W(However)", None], ["W($$)", "W(am)", None], ["W($$)", "W(wish)", None], ["W($$)", "W(would)", None], ["W(am)", "W(glad)", None], ["W(are~1)", "W(been)", "Anaph"], ["W(are~1)", "W(now~1)", None], ["W(are~1)", "W(they)", None], ["W(as)", "W(my~1)", None], ["W(been)", "W(better)", None], ["W(better)", "MW(a_lot)", None], ["W(better)", "W(then)", None], ["W(glad)", "W(are~2)", None], ["W(had)", "W(I~2)", None], ["W(had)", "W(you~1)", None], ["W(have)", "W(been)", None], ["W(my~1)", "W(dentist~1)", None], ["W(teeth)", "W(my~3)", None], ["W(then)", "W(are~1)", None], ["W(wish)", "CBB1", None], ["W(would)", "W(have)", None], ["W(would)", "W(maybe)", None], ["W(would)", "W(teeth)", None]], "nodes": ["CBB1", "CBB2", "MW(a_lot)", "W($$)", "W(However)", "W(I~2)", "W(am)", "W(are~1)", "W(are~2)", "W(as)", "W(been)", "W(better)", "W(dentist~1)", "W(early)", "W(glad)", "W(had)", "W(have)", "W(in)", "W(life)", "W(maybe)", "W(my~1)", "W(my~2)", "W(my~3)", "W(now~1)", "W(on)", "W(teeth)", "W(then)", "W(they)", "W(wish)", "W(would)", "W(you~1)"], "extra_node2words": {}, "node2words": {"W(I~2)": ["I~2"], "W(However)": ["However"], "W(would)": ["would"], "W(then)": ["then"], "W(maybe)": ["maybe"], "W(my~2)": ["my~2"], "W(life)": ["life"], "W(are~1)": ["are~1"], "W(on)": ["on"], "W(as)": ["as"], "W(have)": ["have"], "W(my~3)": ["my~3"], "W(my~1)": ["my~1"], "W(they)": ["they"], "W(in)": ["in"], "W($$)": ["$$"], "W(wish)": ["wish"], "W(had)": ["had"], "W(you~1)": ["you~1"], "W(teeth)": ["teeth"], "W(are~2)": ["are~2"], "W(now~1)": ["now~1"], "W(better)": ["better"], "W(dentist~1)": ["dentist~1"], "MW(a_lot)": ["a", "lot"], "W(glad)": ["glad"], "W(am)": ["am"], "W(been)": ["been"], "W(early)": ["early"]}}
	g5 = {"tokens": ["I~1", "have~1", "purchased", "over", "15", "vehicles", "(", "cars", ",", "rvs", ",", "and~1", "boats", ")", "in", "my", "lifetime", "and~2", "I~2", "have~2", "to", "say", "the~1", "experience", "with", "Michael", "and~3", "Barrett", "Motor", "Cars", "of~1", "San", "Antonio", "was", "one", "of~2", "the~2", "best", "."], "node_edges": [["CBB1", "W(15)", "unspec"], ["CBB1", "W(I~1)", "unspec"], ["CBB1", "W(and~1)", "unspec"], ["CBB1", "W(boats)", "unspec"], ["CBB1", "W(cars)", "unspec"], ["CBB1", "W(have~1)", "cbbhead"], ["CBB1", "W(in)", "unspec"], ["CBB1", "W(lifetime)", "unspec"], ["CBB1", "W(my)", "unspec"], ["CBB1", "W(over)", "unspec"], ["CBB1", "W(purchased)", "unspec"], ["CBB1", "W(rvs)", "unspec"], ["CBB1", "W(vehicles)", "unspec"], ["CBB2", "W(Antonio)", "unspec"], ["CBB2", "W(Barrett)", "unspec"], ["CBB2", "W(Cars)", "unspec"], ["CBB2", "W(Michael)", "unspec"], ["CBB2", "W(Motor)", "unspec"], ["CBB2", "W(San)", "unspec"], ["CBB2", "W(and~3)", "unspec"], ["CBB2", "W(experience)", "unspec"], ["CBB2", "W(of~1)", "unspec"], ["CBB2", "W(the~1)", "unspec"], ["CBB2", "W(with)", "unspec"], ["CBB3", "W(best)", "unspec"], ["CBB3", "W(of~2)", "unspec"], ["CBB3", "W(one)", "unspec"], ["CBB3", "W(the~2)", "unspec"], ["MW(have~2_to)", "W(I~2)", None], ["MW(have~2_to)", "W(say)", None], ["W($$)", "CBB1", None], ["W($$)", "MW(have~2_to)", None], ["W($$)", "W(and~2)", None], ["W(say)", "W(was)", None], ["W(was)", "CBB2", None], ["W(was)", "CBB3", None]], "nodes": ["CBB1", "CBB2", "CBB3", "MW(have~2_to)", "W($$)", "W(15)", "W(Antonio)", "W(Barrett)", "W(Cars)", "W(I~1)", "W(I~2)", "W(Michael)", "W(Motor)", "W(San)", "W(and~1)", "W(and~2)", "W(and~3)", "W(best)", "W(boats)", "W(cars)", "W(experience)", "W(have~1)", "W(in)", "W(lifetime)", "W(my)", "W(of~1)", "W(of~2)", "W(one)", "W(over)", "W(purchased)", "W(rvs)", "W(say)", "W(the~1)", "W(the~2)", "W(vehicles)", "W(was)", "W(with)"], "extra_node2words": {}, "node2words": {"W(San)": ["San"], "W(I~2)": ["I~2"], "W(and~2)": ["and~2"], "W(the~2)": ["the~2"], "W(lifetime)": ["lifetime"], "W(say)": ["say"], "W(Barrett)": ["Barrett"], "W(purchased)": ["purchased"], "W(vehicles)": ["vehicles"], "W(15)": ["15"], "W(Cars)": ["Cars"], "W(experience)": ["experience"], "W(with)": ["with"], "W(over)": ["over"], "W(rvs)": ["rvs"], "W(my)": ["my"], "W(cars)": ["cars"], "W(in)": ["in"], "W(boats)": ["boats"], "W(I~1)": ["I~1"], "W(and~3)": ["and~3"], "W($$)": ["$$"], "W(the~1)": ["the~1"], "W(was)": ["was"], "W(and~1)": ["and~1"], "W(Motor)": ["Motor"], "W(of~1)": ["of~1"], "W(one)": ["one"], "W(have~1)": ["have~1"], "W(Antonio)": ["Antonio"], "MW(have~2_to)": ["to", "have~2"], "W(of~2)": ["of~2"], "W(best)": ["best"], "W(Michael)": ["Michael"]}}
	graphs = [g1,g2,g3,g4,g5][4:5]	# skipping the first one for now, as it has coordination
	for g in graphs:
		f = FUDGGraph(g)
		simplify_coord(f)
		upward(f)
		downward(f)
		for cbb in f.cbbnodes:
			print(cbb.name, cbb.topcandidates, cbb.parentcandidates)
		assert len({n.name for n in f.lexnodes})==len(f.lexnodes)
		for c in f.lexnodes:
			assert not c.isRoot
			#print(c, c.parentcandidates)
		stg = {(p.name,c.name) for c in f.lexnodes for p in c.parentcandidates}
		print(stg)
		print(spanning(stg, '$$'))
		print(len(spanning(stg, '$$')))
		#assert False

if __name__=='__main__':
	test()
