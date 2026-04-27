import modal, sys
print('modal.__version__=', getattr(modal, '__version__', 'unknown'))
print('Has Cls:', hasattr(modal, 'Cls'))
print('Attrs sample:', [a for a in dir(modal) if not a.startswith('_')][:200])
