import chess

class BitReader:
    def __init__(self, bit_stream):
        self.bits = bit_stream
        self.current_position = 0

    def get_next_bit(self):
        if self.current_position >= len(self.bits):
            return None
        bit = self.bits[self.current_position]
        self.current_position += 1
        return bit

def read_file_as_bits(filepath):
    print(f"[*] Reading file: {filepath}")
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()
    print(f"[*] File size: {len(raw_bytes)} bytes")
    bit_stream = []
    for byte in raw_bytes:
        for i in range(7, -1, -1):
            bit = (byte >> i) & 1
            bit_stream.append(bit)
    print(f"[*] Total bits extracted: {len(bit_stream)}")
    return bit_stream

class Stego_Encoder:
    def __init__(self, bit_reader, pacifist_mode=True):
        self.reader = bit_reader
        self.low_bound = 0.0
        self.high_bound = 1.0
        self.pacifist_mode = pacifist_mode # NEW SWITCH
        
    def select_move(self, board):
        all_legal_moves = list(board.legal_moves)
        
        # --- NEW FILTER: PAWN-PUSH STEERING ---
        if self.pacifist_mode:
            steered_moves = []
            for move in all_legal_moves:
                moving_piece = board.piece_at(move.from_square)
                
                # If it's a pawn move, add it to the list TWICE
                if moving_piece.piece_type == chess.PAWN:
                    steered_moves.append(move)
                    steered_moves.append(move)
                else:
                    # Otherwise, just add it once
                    steered_moves.append(move)
                    
            if len(steered_moves) > 0:
                legal_moves = steered_moves
            else:
                legal_moves = all_legal_moves
        else:
            legal_moves = all_legal_moves
            
        num_moves = len(legal_moves)
        
        # 1. Pull 10 bits to form a target fraction
        target_fraction = 0.0
        for _ in range(10):
            bit = self.reader.get_next_bit()
            if bit is not None:
                target_fraction = (target_fraction * 2) + bit
        target_value = target_fraction / 1024.0
        
        # 2. Scale target to current interval
        current_range = self.high_bound - self.low_bound
        scaled_target = self.low_bound + (target_value * current_range)
        
        # 3. Find which bucket the scaled_target falls into
        bucket_size = current_range / num_moves
        
        # SAFETY NET: Prevent Zero Division Error
        if bucket_size <= 0:
            chosen_index = 0
            chosen_move = legal_moves[0]
            return chosen_move
        
        chosen_index = int((scaled_target - self.low_bound) / bucket_size)
        
        if chosen_index >= num_moves:
            chosen_index = num_moves - 1
            
        chosen_move = legal_moves[chosen_index]
        
        # 4. Update bounds
        self.low_bound = self.low_bound + (chosen_index * bucket_size)
        self.high_bound = self.low_bound + bucket_size
        
        return chosen_move

class Stego_Decoder:
    def __init__(self):
        self.low_bound = 0.0
        self.high_bound = 1.0
        self.extracted_bits = []

    def extract_bits_from_move(self, board, played_move):
        all_legal_moves = list(board.legal_moves)
        # For the decoder to match, it must use the baseline (un-duplicated) list
        legal_moves = all_legal_moves
            
        num_moves = len(legal_moves)
        
        played_move_uci = played_move.uci()
        chosen_index = -1
        for i, move in enumerate(legal_moves):
            if move.uci() == played_move_uci:
                chosen_index = i
                break
                
        if chosen_index == -1:
            return

        current_range = self.high_bound - self.low_bound
        bucket_size = current_range / num_moves
        
        self.low_bound = self.low_bound + (chosen_index * bucket_size)
        self.high_bound = self.low_bound + bucket_size
        
        while True:
            if self.high_bound <= 0.5:
                self.extracted_bits.append(0)
                self.low_bound = self.low_bound * 2
                self.high_bound = self.high_bound * 2
            elif self.low_bound >= 0.5:
                self.extracted_bits.append(1)
                self.low_bound = (self.low_bound - 0.5) * 2
                self.high_bound = (self.high_bound - 0.5) * 2
            else:
                break

# --- TEST SCRIPT ---
if __name__ == "__main__":
    my_bits = read_file_as_bits("secret.txt")
    reader = BitReader(my_bits)
    
    engine = Stego_Encoder(reader, pacifist_mode=True)
    decoder = Stego_Decoder()
    
    board = chess.Board()
    played_moves = []
    
    print("--- Starting Steganographic Chess Game ---")
    for turn in range(10):
        move = engine.select_move(board)
        played_moves.append(move)
        board.push(move)
        print(f"Turn {turn+1}: Played {move} (Bits used: {reader.current_position})")
        
    print("\n--- Bob Decoding the Message ---")
    board.reset()
    for move in played_moves:
        decoder.extract_bits_from_move(board, move)
        board.push(move)
        
    print("\nOriginal bits: ", my_bits[:40])
    print("Decoded bits:   ", decoder.extracted_bits[:40])