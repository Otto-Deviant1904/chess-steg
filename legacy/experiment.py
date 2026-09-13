import chess
import random
import math
from engine import Stego_Encoder, BitReader

def run_simulation(num_games=100, max_moves=50, pacifist_mode=True):
    total_theoretical_bits = 0
    total_moves_played = 0
    
    mode_name = "Pawn-Push Steering" if pacifist_mode else "Baseline (Captures Allowed)"
    print(f"\n--- Running Simulation: {mode_name} ---")
    
    for game_num in range(num_games):
        secret_bits = [random.randint(0, 1) for _ in range(5000)]
        reader = BitReader(secret_bits)
        engine = Stego_Encoder(reader, pacifist_mode=pacifist_mode)
        board = chess.Board()
        
        for turn in range(max_moves):
            if board.is_checkmate() or board.is_stalemate() or board.is_insufficient_material():
                break
                
            # Get the legal moves to calculate theoretical capacity
            all_legal_moves = list(board.legal_moves)
            
            if pacifist_mode:
                steered_moves = []
                for move in all_legal_moves:
                    moving_piece = board.piece_at(move.from_square)
                    if moving_piece.piece_type == chess.PAWN:
                        steered_moves.append(move)
                        steered_moves.append(move)
                    else:
                        steered_moves.append(move)
                legal_moves = steered_moves if len(steered_moves) > 0 else all_legal_moves
            else:
                legal_moves = all_legal_moves
                
            num_moves = len(legal_moves)
            
            if num_moves > 1:
                # Calculate theoretical capacity: log2(N)
                bits_capacity = math.log2(num_moves)
                total_theoretical_bits += bits_capacity
            
            # Play the move
            move = engine.select_move(board)
            board.push(move)
            total_moves_played += 1
            
    average_bpm = total_theoretical_bits / total_moves_played if total_moves_played > 0 else 0
    print(f"Total Moves: {total_moves_played} | Total Theoretical Bits: {total_theoretical_bits:.2f} | Avg BPM: {average_bpm:.4f}")
    return average_bpm

if __name__ == "__main__":
    print("=== STEGANOGRAPHY CAPACITY EXPERIMENT ===")
    
    # 1. Run Baseline (Captures Allowed)
    baseline_bpm = run_simulation(num_games=100, max_moves=50, pacifist_mode=False)
    
    # 2. Run Intervention (Pawn-Push Steering)
    pacifist_bpm = run_simulation(num_games=100, max_moves=50, pacifist_mode=True)
    
    print("\n=== FINAL RESULTS ===")
    print(f"Baseline BPM:   {baseline_bpm:.4f}")
    print(f"Pawn-Push BPM:  {pacifist_bpm:.4f}")
    
    increase = pacifist_bpm - baseline_bpm
    percent_increase = (increase / baseline_bpm) * 100 if baseline_bpm > 0 else 0
    
    print(f"Absolute Increase: {increase:.4f} bits/move")
    print(f"Percentage Increase: {percent_increase:.2f}%")