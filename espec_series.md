Parámetros X-13 por serie

┌─────────────────────────┬────────────┬──────────────────────┬─────────────┬────────┬────────────────┐
│          Serie          │  Dataset   │   Modo (transform)   │ Trading-day │ Filtro │ Reproduce col3 │
├─────────────────────────┼────────────┼──────────────────────┼─────────────┼────────┼────────────────┤
│ producción              │ automotriz │ aditivo (none)       │ td1coef     │ s3x5   │ 0,00% ✓        │
├─────────────────────────┼────────────┼──────────────────────┼─────────────┼────────┼────────────────┤
│ ventas                  │ automotriz │ aditivo (none)       │ td1coef     │ s3x5   │ ~1% ⚠️         │
├─────────────────────────┼────────────┼──────────────────────┼─────────────┼────────┼────────────────┤
│ exportaciones           │ automotriz │ aditivo (none)       │ td1coef     │ s3x5   │ 0,00% ✓        │
├─────────────────────────┼────────────┼──────────────────────┼─────────────┼────────┼────────────────┤
│ cemento (despacho nac.) │ cemento    │ multiplicativo (log) │ td (6 coef) │ s3x5   │ 0,00% ✓        │
├─────────────────────────┼────────────┼──────────────────────┼─────────────┼────────┼────────────────┤
│ soja (molienda)         │ granos     │ aditivo (none)       │ ninguno     │ s3x5   │ 0,00% ✓        │
└─────────────────────────┴────────────┴──────────────────────┴─────────────┴────────┴────────────────┘

Constante en las 5: automdl{} + outlier{} + x11{ seasonalma=s3x5 }.

El .spc concreto por serie

# AUTOS (producción / ventas / expo) — aditivo, td1coef
transform{ function=none }
regression{ variables=(td1coef) }
automdl{ } ; outlier{ }
x11{ mode=add seasonalma=s3x5 save=(d11) }

# CEMENTO — multiplicativo, td 6 coef
transform{ function=log }
regression{ variables=(td) }
automdl{ } ; outlier{ }
x11{ seasonalma=s3x5 save=(d11) }

# SOJA — aditivo, SIN trading-day
transform{ function=none }
automdl{ } ; outlier{ }
x11{ mode=add seasonalma=s3x5 save=(d11) }