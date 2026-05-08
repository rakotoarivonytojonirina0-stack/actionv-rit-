#!/usr/bin/env python3
"""
Serveur Action ou Vérité - Version Render.com
"""
import http.server
import json
import os
import socket
import threading
import time
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# Configuration - Adaptée pour Render.com
PORT = int(os.environ.get('PORT', 8080))
HOST = '0.0.0.0'  # Obligatoire pour Render

# État global du jeu
game_state = {
    'players': {},
    'phase': 'lobby',
    'current_turn': None,
    'choice': None,
    'proposals': [],
    'active_proposal': None,
    'proposer_validation': None,
    'history': [],
    'last_update': time.time(),
    'personal_histories': {}
}

state_lock = threading.Lock()

def get_local_ip():
    """Obtenir l'adresse IP - Pas utilisé sur Render"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "0.0.0.0"

class GameHandler(http.server.BaseHTTPRequestHandler):
    
    def do_OPTIONS(self):
        """Gérer les requêtes CORS"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        """Gérer les requêtes GET"""
        parsed = urlparse(self.path)
        
        if parsed.path == '/' or parsed.path == '/index.html':
            self.serve_file('index.html', 'text/html')
        elif parsed.path == '/api/ip':
            # Sur Render, on renvoie l'URL de l'application
            host = self.headers.get('Host', 'localhost')
            self.send_json_response({
                'ok': True,
                'ip': host,
                'port': PORT
            })
        elif parsed.path == '/api/poll':
            self.handle_poll()
        elif parsed.path == '/api/health':
            # Endpoint de santé pour Render
            self.send_json_response({'ok': True, 'status': 'healthy'})
        else:
            self.send_error(404)
    
    def do_POST(self):
        """Gérer les requêtes POST"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'
        
        try:
            data = json.loads(body)
        except:
            data = {}
        
        parsed = urlparse(self.path)
        
        if parsed.path == '/api/join':
            self.handle_join(data)
        elif parsed.path == '/api/start':
            self.handle_start()
        elif parsed.path == '/api/choose':
            self.handle_choose(data)
        elif parsed.path == '/api/propose':
            self.handle_propose(data)
        elif parsed.path == '/api/select_proposal':
            self.handle_select_proposal(data)
        elif parsed.path == '/api/verify':
            self.handle_verify(data)
        elif parsed.path == '/api/reset':
            self.handle_reset()
        elif parsed.path == '/api/validate_action':
            self.handle_validate_action(data)
        else:
            self.send_json_response({'ok': False, 'error': 'Route not found'}, 404)
    
    def serve_file(self, filename, content_type):
        """Servir un fichier statique"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', f'{content_type}; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            # Ajouter des headers de cache pour les fichiers statiques
            if filename.endswith('.html'):
                self.send_header('Cache-Control', 'no-cache')
            else:
                self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except FileNotFoundError:
            self.send_error(404, 'File not found')
    
    def send_json_response(self, data, status=200):
        """Envoyer une réponse JSON"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def handle_poll(self):
        """Polling pour les mises à jour"""
        since = float(parse_qs(urlparse(self.path).query).get('since', [0])[0])
        
        with state_lock:
            self.send_json_response({
                'ok': True,
                'state': game_state,
                'last_update': game_state['last_update']
            })
    
    def handle_join(self, data):
        """Rejoindre la partie"""
        name = data.get('name', '').strip()
        if not name:
            self.send_json_response({'ok': False, 'error': 'Nom requis'})
            return
        
        with state_lock:
            # Limiter le nombre de joueurs pour éviter les abus
            if len(game_state['players']) >= 15:
                self.send_json_response({'ok': False, 'error': 'Partie pleine (max 15 joueurs)'})
                return
            
            if name in game_state['players']:
                game_state['players'][name]['connected'] = True
                self.send_json_response({'ok': True, 'state': game_state})
                return
            
            game_state['players'][name] = {
                'name': name,
                'score': 0,
                'connected': True
            }
            game_state['last_update'] = time.time()
            
            self.send_json_response({'ok': True, 'state': game_state})
    
    def handle_start(self):
        """Démarrer la partie"""
        with state_lock:
            players = game_state['players']
            if len(players) < 2:
                self.send_json_response({'ok': False, 'error': 'Minimum 2 joueurs requis'})
                return
            
            player_names = list(players.keys())
            first_player = player_names[0]
            
            game_state['phase'] = 'choosing'
            game_state['current_turn'] = first_player
            game_state['proposals'] = []
            game_state['active_proposal'] = None
            game_state['last_update'] = time.time()
            
            self.send_json_response({'ok': True, 'state': game_state})
    
    def handle_choose(self, data):
        """Choisir entre action ou vérité"""
        name = data.get('name', '')
        choice = data.get('choice', '')
        
        if choice not in ['action', 'verite']:
            self.send_json_response({'ok': False, 'error': 'Choix invalide'})
            return
        
        with state_lock:
            if game_state['phase'] != 'choosing':
                self.send_json_response({'ok': False, 'error': 'Phase incorrecte'})
                return
            
            if name != game_state['current_turn']:
                self.send_json_response({'ok': False, 'error': "Ce n'est pas votre tour"})
                return
            
            game_state['choice'] = choice
            game_state['phase'] = 'waiting_proposals'
            game_state['proposals'] = []
            game_state['last_update'] = time.time()
            
            self.send_json_response({'ok': True, 'state': game_state})
    
    def handle_propose(self, data):
        """Proposer une action ou vérité"""
        name = data.get('name', '')
        text = data.get('text', '').strip()
        
        if not text:
            self.send_json_response({'ok': False, 'error': 'Proposition vide'})
            return
        
        with state_lock:
            if game_state['phase'] != 'waiting_proposals':
                self.send_json_response({'ok': False, 'error': 'Phase incorrecte'})
                return
            
            if name == game_state['current_turn']:
                self.send_json_response({'ok': False, 'error': "Vous ne pouvez pas proposer à vous-même"})
                return
            
            for prop in game_state['proposals']:
                if prop['author'] == name:
                    self.send_json_response({'ok': False, 'error': 'Vous avez déjà proposé'})
                    return
            
            proposal = {
                'author': name,
                'text': text,
                'timestamp': time.time()
            }
            
            game_state['proposals'].append(proposal)
            game_state['last_update'] = time.time()
            
            self.send_json_response({'ok': True, 'state': game_state})
    
    def handle_select_proposal(self, data):
        """Sélectionner une proposition"""
        name = data.get('name', '')
        index = data.get('index', 0)
        
        with state_lock:
            if game_state['phase'] != 'waiting_proposals':
                self.send_json_response({'ok': False, 'error': 'Phase incorrecte'})
                return
            
            if name != game_state['current_turn']:
                self.send_json_response({'ok': False, 'error': "Ce n'est pas votre tour"})
                return
            
            if index < 0 or index >= len(game_state['proposals']):
                self.send_json_response({'ok': False, 'error': 'Index invalide'})
                return
            
            game_state['active_proposal'] = game_state['proposals'][index]
            game_state['proposer_validation'] = None
            game_state['phase'] = 'verifying'
            game_state['last_update'] = time.time()
            
            self.send_json_response({'ok': True, 'state': game_state})
    
    def handle_verify(self, data):
        """Valider une action ou répondre à une vérité"""
        name = data.get('name', '')
        completed = data.get('completed', None)
        answer = data.get('answer', '')
        
        with state_lock:
            if game_state['phase'] != 'verifying':
                self.send_json_response({'ok': False, 'error': 'Phase incorrecte'})
                return
            
            if not game_state['active_proposal']:
                self.send_json_response({'ok': False, 'error': 'Aucune proposition active'})
                return
            
            is_action = game_state['choice'] == 'action'
            
            if is_action:
                if name != game_state['active_proposal']['author']:
                    self.send_json_response({'ok': False, 'error': 'Seul le proposeur peut valider'})
                    return
                
                if completed is None:
                    self.send_json_response({'ok': False, 'error': 'Validation requise'})
                    return
                
                result = 'accompli' if completed else 'echoue'
                player = game_state['current_turn']
                if completed:
                    game_state['players'][player]['score'] += 1
                
                history_entry = {
                    'player': player,
                    'choice': game_state['choice'],
                    'proposal': game_state['active_proposal']['text'],
                    'proposed_by': game_state['active_proposal']['author'],
                    'result': result,
                    'verified_by': name,
                    'timestamp': time.time()
                }
                
                if player not in game_state['personal_histories']:
                    game_state['personal_histories'][player] = []
                game_state['personal_histories'][player].append(history_entry)
                
                if name not in game_state['personal_histories']:
                    game_state['personal_histories'][name] = []
                game_state['personal_histories'][name].append(history_entry)
                
                game_state['history'].append(history_entry)
                
            else:
                if name != game_state['current_turn']:
                    self.send_json_response({'ok': False, 'error': "Seul le joueur concerné peut répondre"})
                    return
                
                result = 'repondu'
                
                history_entry = {
                    'player': game_state['current_turn'],
                    'choice': game_state['choice'],
                    'proposal': game_state['active_proposal']['text'],
                    'proposed_by': game_state['active_proposal']['author'],
                    'answer': answer,
                    'result': result,
                    'verified_by': name,
                    'timestamp': time.time()
                }
                
                player = game_state['current_turn']
                proposer = game_state['active_proposal']['author']
                
                if player not in game_state['personal_histories']:
                    game_state['personal_histories'][player] = []
                game_state['personal_histories'][player].append(history_entry)
                
                if proposer not in game_state['personal_histories']:
                    game_state['personal_histories'][proposer] = []
                game_state['personal_histories'][proposer].append(history_entry)
                
                public_entry = history_entry.copy()
                public_entry['answer'] = '***'
                game_state['history'].append(public_entry)
            
            # Passer au joueur suivant
            players = list(game_state['players'].keys())
            current_idx = players.index(game_state['current_turn'])
            next_idx = (current_idx + 1) % len(players)
            
            game_state['current_turn'] = players[next_idx]
            game_state['phase'] = 'choosing'
            game_state['choice'] = None
            game_state['proposals'] = []
            game_state['active_proposal'] = None
            game_state['proposer_validation'] = None
            game_state['last_update'] = time.time()
            
            self.send_json_response({'ok': True, 'state': game_state})
    
    def handle_validate_action(self, data):
        """Handle action validation"""
        self.handle_verify(data)
    
    def handle_reset(self):
        """Réinitialiser la partie"""
        with state_lock:
            game_state['players'] = {}
            game_state['phase'] = 'lobby'
            game_state['current_turn'] = None
            game_state['choice'] = None
            game_state['proposals'] = []
            game_state['active_proposal'] = None
            game_state['proposer_validation'] = None
            game_state['history'] = []
            game_state['personal_histories'] = {}
            game_state['last_update'] = time.time()
            
            self.send_json_response({'ok': True, 'state': game_state})
    
    def log_message(self, format, *args):
        """Surcharger pour logger les requêtes"""
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {format%args}")

def run_server():
    """Démarrer le serveur"""
    server = http.server.HTTPServer((HOST, PORT), GameHandler)
    
    print("=" * 60)
    print("🎲 SERVEUR ACTION OU VÉRITÉ - VERSION RENDER")
    print("=" * 60)
    print(f"📍 Serveur démarré sur le port {PORT}")
    print(f"📍 Health check: http://0.0.0.0:{PORT}/api/health")
    print("=" * 60)
    print("Appuyez sur Ctrl+C pour arrêter le serveur")
    print("=" * 60)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Serveur arrêté")
        server.shutdown()

if __name__ == '__main__':
    run_server()