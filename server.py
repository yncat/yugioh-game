import re
import gsb
from gsb.intercept import Reader
from twisted.internet import reactor
import duel as dm

active_duel = None
duelp0 = None
duelp1 = None

class MyServer(gsb.Server):
	def on_connect(self, caller):
		self.notify(caller.connection, "Connected!")

server = MyServer(port=4000)
# Map of duels to tuple of players
duels = {}
@server.command(r'^duel *$')
def duel(caller):
	global active_duel, duelp0, duelp1
	if active_duel is None:
		active_duel = MyDuel()
		active_duel.load_deck(0, dm.deck)
		caller.connection.notify('Duel created. You are player 0.')
		active_duel.players[0] = caller.connection
		caller.connection.duel_player = 0
		duelp0 = caller.connection
		caller.connection.duel = active_duel
	else:
		caller.connection.notify("Joining duel as player 1 and starting.")
		active_duel.load_deck(1, dm.deck)
		caller.connection.duel_player = 1
		duelp1 = caller.connection
		active_duel.players[1] = caller.connection
		duels[active_duel] = (duelp0, duelp1)
		caller.connection.duel = active_duel
		active_duel.start()
		procduel(active_duel)

def procduel(d):
	while True:
		res = d.process()
		if res & 0x10000:
			if d.keep_processing:
				d.keep_processing = False
				continue
			break

class MyDuel(dm.Duel):
	def __init__(self):
		super(MyDuel, self).__init__()
		self.keep_processing = False
		self.to_ep = False
		self.to_m2 = False
		self.current_phase = 0
		self.cm.register_callback('draw', self.draw)
		self.cm.register_callback('phase', self.phase)
		self.cm.register_callback('new_turn', self.new_turn)
		self.cm.register_callback('idle', self.idle)
		self.cm.register_callback('select_place', self.select_place)
		self.cm.register_callback('select_chain', self.select_chain)
		self.cm.register_callback('summoning', self.summoning)
		self.cm.register_callback("select_battlecmd", self.select_battlecmd)
		self.cm.register_callback('attack', self.attack)
		self.cm.register_callback('begin_damage', self.begin_damage)
		self.cm.register_callback('end_damage', self.end_damage)
		self.cm.register_callback('battle', self.battle)
		self.cm.register_callback('damage', self.damage)
		self.cm.register_callback('hint', self.hint)
		self.cm.register_callback('select_card', self.select_card)

		self.players = [None, None]
		self.lp = [8000, 8000]

	def draw(self, player, cards):
		pl = self.players[player]
		pl.notify("Drew %d cards:" % len(cards))
		for i, c in enumerate(cards):
			pl.notify("%d: %s" % (i+1, c.name))
		self.players[1 - player].notify("Opponent drew %d cards." % len(cards))

	def phase(self, phase):
		phases = {
			1: 'draw',
			2: 'standby',
			4: 'main1',
			8: 'battle start',
			0x10: 'battle step',
			0x20: 'damage',
			0x40: 'damage calculation',
			0x80: 'battle',
			0x100: 'main2',
			0x200: 'end',
		}
		phase_str = phases.get(phase, str(phase))
		for pl in self.players:
			pl.notify('entering %s phase.' % phase_str)
		self.current_phase = phase

	def new_turn(self, tp):
		self.tp = tp
		for pl in self.players:
			pl.notify("Player %d is tp." % tp)

	def idle(self, summonable, spsummon, repos, idle_mset, idle_set, idle_activate, to_bp, to_ep, cs):
		self.state = "idle"
		pl = self.players[self.tp]
		self.summonable = summonable
		self.spsummon = spsummon
		self.repos = repos
		self.idle_mset = idle_mset
		self.idle_set = idle_set
		self.idle_activate = idle_activate
		self.to_bp = bool(to_bp)
		self.to_ep = bool(to_ep)

		self.idle_action(pl)

	def idle_action(self, caller):
		pl = self.players[self.tp]
		pl.notify("Select a card on which to perform an action.")
		if self.to_bp:
			pl.notify("b: Enter the battle phase.")
		if self.to_ep:
			pl.notify("e: End phase.")
		def r(caller):
			if caller.text == 'b' and self.to_bp:
				self.set_responsei(6)
				reactor.callLater(0, procduel, self)
				return
			elif caller.text == 'e' and self.to_ep:
				self.set_responsei(7)
				reactor.callLater(0, procduel, self)
				return
			loc, seq = self.cardspec_to_ls(caller.text)
			if loc is None:
				pl.notify("Invalid specifier. Retry.")
				pl.notify(Reader, r)
				return
			card = self.get_card(self.tp, loc, seq)
			self.act_on_card(caller, card)
		pl.notify(Reader, r)

	def act_on_card(self, caller, card):
		pl = self.players[self.tp]
		pl.notify(card.name)
		if card in self.summonable:
			pl.notify("s: Summon this card in face-up attack position.")
		if card in self.idle_mset:
			pl.notify("m: Summon this card in face-down defense position.")
		if card in self.repos:
			self.notify("r: reposition this card.")
		def action(caller):
			if caller.text == 's' and card in self.summonable:
				self.set_responsei(card.sequence << 16)
			elif caller.text == 'm' and card in self.idle_mset:
				self.set_responsei((card.sequence << 16) + 3)
			elif caller.text == 'r' and card in self.repos:
				self.set_responsei((card.sequence << 16) + 2)
			else:
				pl.notify("Invalid action.")
				pl.notify(Reader, action)
				return
			reactor.callLater(0, procduel, self)
		pl.notify(Reader, action)

	def cardspec_to_ls(self, text):
		r = re.search(r'^([a-z]+)(\d+)', text)
		if not r:
			return (None, None)
		if r.group(1) == 'h':
			l = dm.LOCATION_HAND
		elif r.group(1) == 'm':
			l = dm.LOCATION_MZONE
		elif r.group(1) == 's':
			l = dm.LOCATION_SZONE
		else:
			return None, None
		return l, int(r.group(2))

	def pcl(self, name, cards):
		self.players[self.tp].notify(name+":")
		for card in cards:
			self.players[self.tp].notify(card.name)

	def select_place(self):
		pl = self.players[self.tp]
		pl.notify("Select place for card")
		def r(caller):
			l, s = self.cardspec_to_ls(caller.text)
			if l is None:
				pl.notify("Invalid cardspec. Try again.")
				pl.notify(Reader, r)
				return
			resp = bytes([self.tp, l, s])
			self.set_responseb(resp)
			reactor.callLater(0, procduel, self)
		pl.notify(Reader, r)

	def select_chain(self, player, size, spe_count):
		if size == 0 and spe_count == 0:
			self.keep_processing = True
			self.set_responsei(-1)
		else:
			self.players[player].notify("select chain")

	def summoning(self, card, location):
		pos = str(hex(location))
		for pl in self.players:
			pl.notify("Player %d summoning %s in %s" % (self.tp, card.name, pos))

	def select_battlecmd(self, player, activatable, attackable, to_m2, to_ep):
		self.state = "battle"
		self.activatable = activatable
		self.attackable = attackable
		self.to_m2 = bool(to_m2)
		self.to_ep = bool(to_ep)
		pl = self.players[player]
		self.pcl("activatable", activatable)
		self.pcl("attackable", attackable)

	def attack(self, attacker, target):
		self.players[self.tp].notify("Attack: attacker=%x target=%x" % (attacker, target))

	def begin_damage(self):
		self.notify_all("begin damage")

	def end_damage(self):
		self.notify_all("end damage")

	def battle(self, attacker, aa, ad, bd0, tloc, da, dd, bd1):
		loc = (attacker >> 8) & 0xff
		seq = (attacker >> 16) & 0xff
		c2 = attacker & 0xff
		card = self.get_card(c2, loc, seq)
		tc = tloc & 0xff
		tl = (tloc >> 8) & 0xff
		tseq = (tloc >> 16) & 0xff
		if tloc:
			target = self.get_card(tc, tl, tseq)
		else:
			target = None
		s = "%s (%d/%d) attacks" % (card.name, aa, ad)
		if target:
			s += " %s (%d/%d)" % (target.name, da, dd)
		self.notify_all(s)

	def damage(self, player, amount):
		self.notify_all("Player %d's lp decreased by %d, now %d" % (player, amount, self.lp[player]-amount))
		self.lp[player] -= amount

	def notify_all(self, s):
		for pl in self.players:
			pl.notify(s)

	def hint(self, msg, player, data):
		if msg == 3 and data == 501:
			self.players[player].notify("Select a card to discard:")

	def select_card(self, player, cancelable, min, max, cards):
		con = self.players[player]
		con.notify("Select %d to %d cards separated by spaces:" % (min, max))
		for i, c in enumerate(cards):
			con.notify("%d: %s" % (i+1, c.name))
		def f(caller):
			cds = caller.text.split()
			buf = bytes([len(cds)])
			for i in cds:
				i = int(i) - 1
				buf += bytes([i])
			self.set_responseb(buf)
			procduel(self)
		con.notify(Reader, f)

@server.command('^h(and)?$')
def hand(caller):
	con = caller.connection
	h = con.duel.get_cards_in_location(con.duel_player, dm.LOCATION_HAND)
	if not h:
		con.notify("Your hand is empty.")
		return
	for c in h:
		con.notify("h%d: %s" % (c.sequence + 1, c.name))

def check_tp(f):
	def wraps(caller):
		if caller.connection.duel_player != caller.connection.duel.tp:
			caller.connection.notify("It's not your turn.")
			return
		f(caller)
	return wraps

@server.command(r'^summon ([a-z]+)(\d+)$')
@check_tp
def summon(caller):
	idle_action(caller, 'summon', 'summonable', 0)

@server.command(r'^mset ([a-z]+)(\d+)$')
@check_tp
def mset(caller):
	idle_action(caller, 'mset', 'idle_mset', 3)

def idle_action(caller, name, list_name, add):
	loc, n = caller.args
	n = int(n)
	duel = caller.connection.duel
	hand = duel.get_cards_in_location(caller.connection.duel_player, dm.LOCATION_HAND)
	seq = n - 1
	hc = [card for card in hand if card.sequence == seq]
	summonable = getattr(duel, list_name)
	summonable = [t for t in summonable if t[3] == seq]
	if not summonable:
		caller.connection.notify("Cannot %s %s." % (name, hc[0].name))
		return
	card, controller, location, sequence = summonable[0]
	i = (sequence << 16) + add
	duel.set_responsei(i)
	procduel(duel)

@server.command(r'^place ([a-z]+)(\d+)')
@check_tp
def place(caller):
	duel = caller.connection.duel
	place, n = caller.args
	n = int(n) - 1
	if n < 0 or place not in ('m', 's'):
		caller.connection.notify("Invalid place.")
		return
	l = p = 0
	if place == 'm':
		p = 4
	elif place == 's':
		p = 8
	resp = bytes([caller.connection.duel_player, p, n])
	duel.set_responseb(resp)
	procduel(duel)

@server.command(r'^bp$')
@check_tp
def bp(caller):
	duel = caller.connection.duel
	if not duel.to_bp:
		caller.connection.notify("Unable to enter battle phase.")
		return
	duel.set_responsei(6)
	procduel(duel)

@server.command(r'^ep$')
@check_tp
def ep(caller):
	duel = caller.connection.duel
	if not duel.to_ep:
		caller.connection.notify("Unable to enter end phase.")
		return
	if duel.current_phase == 8:
		duel.set_responsei(3)
	else:
		duel.set_responsei(7)
	procduel(duel)

@server.command(r'^m2$')
@check_tp
def m2(caller):
	duel = caller.connection.duel
	if not duel.to_m2 or duel.current_phase != 8:
		caller.connection.notify("Unable to enter main2 phase.")
		return
	duel.set_responsei(2)
	procduel(duel)

@server.command('^tab$')
def tab(caller):
	duel = caller.connection.duel
	mz = duel.get_cards_in_location(caller.connection.duel_player, dm.LOCATION_MZONE)
	for card in mz:
		caller.connection.notify("m%d: %s (%d/%d)" % (card.sequence+1, card.name, card.attack, card.defense))

@server.command(r'^attack (\d+)$')
def attack(caller):
	duel = caller.connection.duel
	n = int(caller.args[0])
	if n < 1 or n > len(duel.attackable):
		caller.connection.notify("Invalid card.")
		return
	c = duel.attackable[n - 1]
	duel.set_responsei((c[3] << 16) + 1)
	procduel(duel)

if __name__ == '__main__':
	server.run()