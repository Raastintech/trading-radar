"""
Mode-aware scanner

Allows scanning with specific trading mode
"""

from trading_mode import TradingModeManager
from signal_generator import MasterSignalGenerator
from veto_council import VetoCouncil

def scan_with_mode(watchlist, mode='SHORT_TERM'):
    """
    Scan watchlist with specific trading mode
    """
    
    print(f"\n{'='*80}")
    print(f"🔎 SCANNING WITH {mode} MODE")
    print(f"{'='*80}\n")
    
    # Initialize with mode
    signal_gen = MasterSignalGenerator(default_mode=mode)
    council = VetoCouncil()
    
    mode_config = signal_gen.mode_manager.get_mode(mode)
    
    print(f"📋 Mode Details:")
    print(f"   Hold Period: {mode_config.hold_period_min}-{mode_config.hold_period_max} days")
    print(f"   Profit Targets: {mode_config.profit_target_1*100:.0f}% / {mode_config.profit_target_2*100:.0f}%")
    print(f"   Min Confluence: {mode_config.min_confluence}")
    print()
    
    aligned = []
    
    for ticker in watchlist:
        try:
            # Generate signal in current mode
            signal = signal_gen.generate_signal(ticker)
            
            if not signal:
                continue
            
            # Evaluate with council
            decision = council.evaluate_trade(ticker, show_details=False)
            
            if decision['decision'] == 'EXECUTE':
                print(f"✅ {ticker}: ALIGNED ({mode} mode)")
                print(f"   Entry: ${signal['entry_price']:.2f}")
                print(f"   Targets: ${signal['target_1']:.2f} / ${signal['target_2']:.2f}")
                print(f"   Hold: {signal['hold_period'][0]}-{signal['hold_period'][1]} days\n")
                
                aligned.append({
                    'ticker': ticker,
                    'signal': signal,
                    'decision': decision
                })
        
        except Exception as e:
            continue
    
    print(f"\n{'='*80}")
    print(f"📊 {mode} MODE RESULTS: {len(aligned)} aligned")
    print(f"{'='*80}\n")
    
    return aligned


if __name__ == "__main__":
    watchlist = ['AAPL', 'NVDA', 'TSLA', 'COIN', 'RKLB']
    
    # Scan in SHORT_TERM mode
    short_term_trades = scan_with_mode(watchlist, mode='SHORT_TERM')
    
    # Scan in SWING mode
    swing_trades = scan_with_mode(watchlist, mode='SWING')