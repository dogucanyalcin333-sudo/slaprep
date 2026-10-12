# SLAPrep Ar-Ge notları

Amaç: daha verimli plate kullanımı ve daha uygun delikler. Her değişiklik gerçek kitlerde ölçüldü
(`tools/bench_layout.py`, `tools/rnd_stats.py`); sonuçlar aşağıda.

## 1. Plate yerleşimi

### Ölçütler
- **Plate sayısı**: Her plate ayrı bir baskı demek; en büyük etken bu.
- **Σ yükseklik**: Her plate'teki en yüksek parçaların toplamı. MSLA'da baskı süresi katman sayısıyla, yani o
  plate'teki en yüksek parçayla belirlenir; bu yüzden toplam baskı süresinin göstergesi olarak kullanıldı.
- **Alan alt sınırı**: (siluet + yarım boşluk payı) alanlarının toplamı / kullanılabilir plate alanı.

### Yapılanlar
| # | Değişiklik | Neden |
|---|---|---|
| 1 | Döndürme 90° yerine 30° adımla (12 açı) | Uzun/çapraz parçalar (kılıç, ejderha gövdesi) |
| 2 | "Sol-alt" yerine **en çok temas** konumu (parça kenara ve diğer parçalara yaslanır) | Düzensiz siluetlerde boşlukları doldurur |
| 3 | 5 farklı sıralama (alan, yükseklik, uzunluk, 2 karışık) × 2 strateji; en iyisi seçilir | Tek sıralama yerel optimumda kalıyordu |
| 4 | Seçim ölçütü: sığmayan → plate → Σ yükseklik → doluluk | Aynı plate sayısında kısa parçaları ayrı plate'e toplar |
| 5 | **Global yön seçimi**: plate sayısı alt sınırın üstündeyse büyük izli parçalar, puanı en iyiye yakın (≤ 0.6) ve izi daha küçük alternatif yönlerine çevrilir; sadece plate azalırsa kabul edilir | Yön her parça için tek başına seçildiğinde plate verimi kaybediliyordu |

### Ölçümler (aynı yönlendirilmiş parçalarla, M7 223×126, boşluk 3 mm)
| Kit | Eski | Yeni (1–4) | Not |
|---|---|---|---|
| Rango (18 parça) | 3 plate, Σ 351 mm | 3 plate, **Σ 321 mm** (−%9) | alt sınır 2.02 |
| Malenia (11) | 2 plate, Σ 245 mm | **1 plate**, Σ 141 mm | (5) yeni yönlerle 2 → 1 plate: Torso alternatif yöne çevrildi |
| Guts (20) | 2 plate, Σ 184 mm | 2 plate, **Σ 172 mm** (−%7) | iz plate'in %74'ü, boşluk payıyla %90: 1 plate gerçekçi değil |
| Sanji (20) | 2 plate | 2 plate | HEIGHT_PILLAR zaten sığmıyor |

Gözlemler: Açı adımını 30°'den 15°'ye indirmek hiçbir kitte ek kazanç getirmedi, süreyi 2 katına çıkardı.
Kazancı sağlayan temas stratejisi ile çoklu sıralama.

## 2. Delikler

### Yapılanlar
| # | Değişiklik | Neden |
|---|---|---|
| 1 | **Sayı**: kavite hacmi (~20 ml başına +1) ve uzunluğuna (50 mm başına +1) göre, 2–8 arası | Büyük kavitelerde 2–4 delik yetersizdi (kullanıcı geri bildirimi) |
| 2 | **Çap otomatik**: < 10 ml → 3 mm, < 40 ml → 4 mm, üstü → 5 mm. Her çap için güvenli bölge ayrı hesaplanır; toplam açıklığı (adet × çap²) en büyük olan seçilir | Uygun yerlerde daha geniş drenaj |
| 3 | **Vantuz çanakları** (topolojik persistence): kavitenin baskı yönündeki her yerel minimumu (≥ 2 mm derin) ayrı bir vantuzdur, her birinin dibine delik açılır | Eski kural sadece tek global en alçak noktaya delik koyuyordu; kıvrık kavitelerde (ejderha gövdesi, U/∩ biçimler) diğer çanaklar delinmeden kalıyordu |
| 4 | **Reçine cepleri**: yerel maksimumlar (parça baş aşağı sarkarken reçine biriken yerler) ikinci öncelik | Drenaj |
| 5 | Yönlendirme skoru: o yönde çanakların kaçının gizli bölgeden deliklenebildiği | Delik kuralı yön seçimine geri besleniyor |
| 6 | Pim tepesi tespiti: yüzeyin 1 mm altından yanlara atılan ışınlar hemen karşı parçaya çarpıyorsa pim | Bottle'ın 23 mm pimi kaçıyordu, tepesine delik açılıyordu |
| 7 | Duvar modu: duvara bakan yüzler (3 mm çevrede yumuşatılmış normal) gizli; askı yuvası (alternatife göre oyulmuş bölge) korunur | chiro duvar sürümü |

| # | Değişiklik | Neden |
|---|---|---|
| 8 | Delik ağzı çemberinin tamamı yüzeyde olmalı (çember noktalarından eksen yönünde atılan ışınlar teğet düzlemden ≤ 1 mm sapmayla yüzeye değmeli) | Ek yüzeylerinin kenarında ağız kenardan taşıyordu (chiro'da 4 parça) |
| 9 | Ağız kontrolü artık en yakın nokta yerine delik ekseni boyunca yapılıyor | Pürüzlü yüzeyde en yakın nokta yandaki tüy sivrisine kayıyordu |
| 10 | Çanak sayılması için kesit alanı ≥ 25 mm² | Vakum kuvveti kesit alanıyla orantılı; iğne ucu kadar cepler önemsiz |

## 3. Final sonuçlar (son kodla, 5 kit)

| Kit | Plate (önce → sonra) | Reçine | Delik (açıklık) | Vantuz çanağı delikli | Başarısız kontrol |
|---|---|---|---|---|---|
| Rango (tam boy) | 3 → 3 (alt sınır 3) | 964 → 393 ml | 37 (498 mm²); önce 22 × 3 mm = 155 mm² | 10/11 | Head_V1: 1 çanak |
| Malenia | 2 → **1** | 236 → 129 ml | 13 (64 mm²) | 8/9 | Torso: 1 çanak |
| Sanji | 2 → 2 (yeni yönlerle 3 olurdu; global seçim 2'de tuttu) | 1149 → 508 ml | 42 (230 mm²) | 17/21 | 4 parça çanak |
| Guts | 2 → 2 | 259 → 164 ml | 17 (77 mm²) | 11/13 | arm_with_sword onarılamayan girdi |
| chiro (duvar, 300 mm) | 4 → 4 (Chihiro 1 + Dragon 3) | 681 → 302 ml | 37 (235 mm²) | 16/17 | Dragon_13_Key: 1 çanak |

Tüm plate'lerde parça arası ≥ 3.3 mm. Tüm deliklerin ağzı montajda gizli, pimlerde delik yok, tüm çıktılar
watertight (onarılamayan girdi hariç). "Vantuz çanağı" başarısızlıkları yeni bir teşhis: eski yöntem bu çanakları
hiç görmüyordu. Şimdi her biri raporda konumuyla belirtiliyor; gizli bölgeden deliklenemedikleri için bir karar
gerekiyor (görünür delik izni ya da kabul).

## 4. Otomatik montaj (montaj bilgisi olmayan kitler)

Bazı kitlerde her parça ayrı export edilip orijine ortalanmıştır (1/9 Radahn); boy hesaplanamaz. `autoassemble.py`
parçaları kendi başına birleştirmeye çalışır (varsayım: parçalar sadece ötelenmiş, döndürülmemiş):

1. **Ek yüzeyi adayları**: her parçada büyük düz bölgeler (stüdyolar parçaları düz kesimlerle böler).
2. **Eşleştirme**: aynı kesimden çıkan iki yüzün normalleri terstir ve kesim çizgisi aynıdır → iki yüzün düzleme
   izdüşümü 2B FFT korelasyonuyla hizalanır → B'nin A'ya göre 3B ötelemesi (pim/yuva şekli konumu sabitler).
3. **Artımlı montaj**: kökten (en büyük parça) başlayıp her adımda en güçlü oturan parça eklenir; her aday TÜM
   yerleşmiş parçalara karşı puanlanır (temas − 3 × iç içe geçme) → yanlış yer başka parçaya gömüleceği için elenir.
4. **Bundle adjustment**: her eklemeden sonra yeni parça ve komşuları birlikte ince ayarlanır (zincirdeki kayma
   birikmesin); sonda tüm montaj.
5. **İki aşama**: önce sadece güçlü ekler (≥ 300 mm² temas) kurulur, sonra zayıflar. Açgözlü büyüme zayıf bir
   teması asıl güçlü ekten önce kapmasın (Radahn: Miquella gövdesi kılıca 78 mm² ↔ kendi saçına 897 mm²).
6. **Alt montajlar**: ana montaj tıkanınca kalanlardan alt montajlar kurulur (iç ekleri her zaman güçlü), rijit
   gövde olarak ana montaja ya da birbirine eklenir. Ana montaj her büyüdüğünde yeniden kurulur. Zayıf aşamada
   alt montajdan tek parça koparılmaz, gruplar sadece bütün olarak eklenir.
7. **3B FFT kenetlenme**: düz eki olmayan, organik temaslı parça VE alt montajlar (kuma gömülü taş, kaideye organik
   yüzeyle oturan kaya) için tüm ötelemeler tek seferde taranır. Izgara her grubun boyutuna göre kurulur, mesafe
   sadece montaj kutusunda hesaplanır. Zayıf aşamada tek parçalar alt montajlara da kenetlenebilir (en iyi yer).
8. **Fiziksel kurallar**: zemin kuralı (kaide tabanının altına parça inemez), büyüklüğe göre temas eşiği,
   olası alternatifler (Torso/Torso_v2) birbirine karşı cezalandırılmaz, sonradan gerçek konumda doğrulanır.
9. **Kabul**: parçaların ≥ %60'ı ya da hacmin ≥ %50'si ana montajdaysa boy ana montajdan ölçülür; ayrı kalan
   gruplar raporda kendi boylarıyla listelenir ve delikleri grup içi temaslara göre açılır.

**Doğrulama (Rango, gerçek montaj bilinen kit; her parça yapay olarak orijine taşındı):** 18 parçanın 17'si doğru
yerde, medyan hata 1.6 mm, en büyük 2.9 mm, hiç yanlış yerleşim yok (yalnız sol el yerleşmedi).

**Radahn (32 parça, her biri orijine ortalanmış):** 29/32 parça yerleşiyor. Kaide (4 çeyrek + 2 kılıç) → ayaklar →
bacaklar → gövde → kollar/kafa; bel eki %95 siluet uyumu, 2268 mm² temas; kaidenin bir çeyreği organik yüzeyle
(kenetlenme, ~4900 mm²). Kitte **iki sürüm** var ve araç bunu kendisi buldu: Miquella + 7 saç bloğu Head_v2'ye
%94 siluet uyumu / 1576 mm² ile oturuyor, Head_v1'in yelesine gömülüyor → V2 (Head_v2 + aynı klasördeki Torso_v2
+ Miquella) varsayılan, boy **261.5 mm**; V1 (Head_v1, Miquella yok) **245.8 mm**.
Engel olan hatalar: (1) Radahn Legs'in analiz kopyası (2.2M → 80k üçgen) kenar daraltmada çöküyordu (4.7 mm sapma,
%10 hacim kaybı) → bel eki görünmüyordu; sadeleştirme artık sapmayı ölçüyor, tutmazsa voxel kümeleme + daraltma
(0.09 mm). (2) Kaba ızgarada ince saç telleri "iç" hücre üretmiyordu → kenetlenme saça gömülü yanlış yerleri
seçiyordu; iç eşiği 0.3 hücreye indirildi. Süre: Radahn montajı ~10 dk (bir kez; önbelleğe alınır).

## 5. Sürdürülebilirlik: doğrulama ve kullanıcı düzeltmesi

Amaç: araç bir kitte yanılınca bunu kullanıcı görsün ve kendisi düzeltebilsin; geliştirici tarafında da bir
düzeltme başka bir kiti sessizce bozmasın.

- **Regresyon seti** (`tools/regress.py`, `tools/regress_kits.yaml`): bilinen kitlerde montaj modu, yukarı ekseni,
  boy, yerleşen sayısı, seçili/alternatif ve kaide parçaları beklenenle karşılaştırılır; Rango'nun bozulup otomatik
  montajla geri kurulması (gerçek konumla hata) da ölçülür. Her beklentinin nasıl doğrulandığı yazılıdır;
  doğrulanmamış olanlar "değişiklik bekçisi" diye işaretlenir.
- **Mekanizma testleri** (`tests/test_autoassemble.py`, yapay parçalar, saniyeler): alternatif-farkında ekleme,
  tercih + klasör eşi seçimi, grup kenetlenmesi, kullanıcı bağlantısı/ayırma, sadeleştirme sapması, kontrol listesi.
- **Montaj kontrolü** (uygulama + rapor): her parça için nasıl yerleştiği (ek yüzeyi / kenetlenme / kullanıcı /
  dosyadan) ve temas alanı; güçlü ek < 300 mm² ya da organik temas < 1000 mm² "kontrol edin". Kullanıcı
  düzeltmeleri: ayır (`--detach`), bağla (`--join ÇAPA:PARÇA[:SIRA]`: parça sadece çapayla teması aranarak
  yerleşir). Kullanıcı bağlantısında çapaya pim/yuva toleransı kadar gömülmeye izin var (temasın %25'i + 5 mm²;
  Casca'nın eli bileğe 82 mm² temas / 10 mm² gömülme ile oturuyor), diğer parçalara katı kural. Aynı montaja
  bağlanan parçalar birlikte çözülür (en iyi 4 aday, birbirine gömülmeyen en iyi atama: iki el iki bileğe).
  Adaylar parça boyunun %30'undan (≥ 4 mm) yakınsa aynı konum sayılır; uygulama "konum 2/3" gösterir.
  Kullanıcının bağladığı parça doğrulamada "zayıf temas" diye geri ayrılmaz (uyarı verilir).
  Casca'da denendi: pelerin omuzlara oturup arkaya dalgalanıyor, eller bileklere; doğruluğu fotoğrafla kontrol
  edilmeli (stüdyo görseli yok).
- **Fotoğrafla karşılaştırma**: montajın 8 açıdan gri görünümü stüdyo fotoğrafıyla yan yana. Fotoğraftan konum
  çözülmez (ölçek/perspektif bilinmez), ama büyük hatalar bir bakışta görünür. İlk yakaladığı: Sanji'deki
  `HEIGHT_PILLAR` (stüdyonun 300 mm ölçek çubuğu, 162 ml, basılacaklar listesindeydi) → ölçek referansı tespiti
  (düz prizma + figüre değmiyor + uzun ince/adı referans gibi) eklendi.
- Sonraki adım: fotoğraf siluetiyle otomatik karşılaştırma (en uygun açıda siluet örtüşmesi düşükse "montajda eksik
  grup olabilir" uyarısı).

## 6. Baskı süresi odaklı plate yerleşimi (2026-10-04)

SLA'da bir plate'in süresi **en yüksek parçasıyla** belirlenir (katman sayısı); kısa parçalar uzun bir plate'in
boşluğuna bedavaya sığar. Eklenenler:
- Süre modeli (`layout.TimeModel`): katman kalınlığı × katman süresi + plate başına ek süre (varsayılan 0.05 mm,
  6.5 sn, 15 dk; uygulamada Yazıcı sekmesinden). Plate başına en yüksek parça ve tahmini süre rapor/uygulamada.
- Hedef seçimi: **en kısa toplam süre** (varsayılan) ya da **en az plate**.
- Yükseklik sıkıştırması: kısa bir plate'i "uzatan" parça daha yüksek bir plate'te yer varsa oraya taşınır;
  plate boşalırsa kalkar (yapay örnek: 250 → 160 mm).
- Yön: puanı yakın ve ≥ %10 daha alçak yön alternatifleri; plate'in süresini belirleyen uzun parçalar alçaltılabilir.
- Klasörlere göre ayrılmışsa "tek grup dizilseydi" karşılaştırması raporda.

**Kıyaslama** (`tools/bench_plates.py`, 8 kitin önceki hazırlıklarındaki yönlendirilmiş parçalar):

| Kit | Eski | Yeni (en kısa süre) | Klasörsüz |
|---|---|---|---|
| Rango (18) | 3 plate, 11 sa 26 dk | aynı | – |
| Sanji (20) | 2 plate, 12 sa 45 dk | aynı | – |
| Guts (20) | 2 plate, 6 sa 38 dk | aynı | – |
| Malenia (11) | 1 plate, 5 sa 20 dk | aynı | – |
| Casca (9) | 2 plate, 6 sa 41 dk | aynı | – |
| Godfrey (26) | 4 plate, 17 sa 51 dk | aynı | – |
| chiro (27) | 4 plate, 11 sa 21 dk | aynı | **3 plate, 9 sa 34 dk** |
| Radahn (30) | 6 plate, 23 sa 22 dk | aynı | **5 plate, 19 sa 09 dk** |

Dürüst sonuç: atama iyileştirmesi bu kitlerde fark yaratmadı — eski yerleştirici de eşitlikte toplam yüksekliğe
bakıyordu ve bu kitlerde uzunlar zaten bir arada (yapay 3 uzun + 1 orta + 2 kısa örneğinde 14 sa 07 → 11 sa 09).
En büyük kaldıraç **klasörlere ayırmamak** (chiro + Radahn: −2 plate, −6 sa); bu kullanıcı tercihi olduğu için
sadece bildiriliyor.

**Yön + yerleşim birlikte (gerçek hazırlık):** Sanji 2 plate 12 sa 45 dk → **2 plate 10 sa 33 dk** (−%17). Belirleyici:
en uzun parça LEGS puanı yakın daha alçak bir yönle (145 → 94 mm; delik/kontroller temiz). İki ders:
- "En kısa süre" ilk denemede 3 plate / 12 sa 46 dk seçti (2 plate'ten 1 dk kısa) → **plate başına 60 dk karar
  eşiği** (fazladan plate ancak belirgin kazançla).
- Açgözlü yön araması yola bağlı: süreyi izleyen arama başka yola sapıp 3 plate'te kalıyordu → plate sayısını ve
  süreyi izleyen iki arama yapılıp sonuçları aynı ölçütle karşılaştırılıyor.

**Alt klasörlere göre plate (otomatik):** artık sadece süreye/plate'e mal olmuyorsa ayrılır (`layout.prefer_folders`);
Radahn otomatikte tek grup seçti (5 plate). Kullanıcı "evet" derse her koşulda ayrılır.
**Derin yerleşim araması** (15° açı, 8 sıralama) denendi: Godfrey/chiro'da kazanç yok, 3–4 kat yavaş → eklenmedi.

## 6b. Onarım sadakati (2026-10-04)

Kullanıcı Hulkbuster torsonun bozuk çıktığını gördü: pymeshfix (`clean_from_arrays`) kendini kesen bölgeleri silip
kalan delikleri kapatıyordu → göğüs plakası, omuz yuvaları gibi yüzeylerin **%19.5–29'u kayboluyordu**; kontrol
sadece "su geçirmez + boolean'a uygun" olduğu için bu sessizce kabul ediliyordu.

- **Sadakat ölçüsü** (`io.repair_fidelity`): orijinal yüzeyden 60 000 örnek → onarılmış katının dışında kalanlar
  (> 0.5 mm) = *kaybolan*; onarılmış yüzeyden örnekler → orijinale > 0.5 mm uzak olanlar = *eklenen* (uydurma kapak).
  Kabul: kaybolan ≤ %1, eklenen ≤ %15.
- **Onarım zinciri** (`io._try_repair`), her adım sadakat kontrollü, ilk geçen alınır:
  1. hızlı: kırıntı at, köşe birleştir, küçük delik doldur, yön düzelt;
  2. çok gövdeli parça: gövdeleri ayrı kapatıp manifold birleşimi (iç içe geçen kabuklar);
  3. sadece delik doldurma (PyTMesh `fill_small_boundaries`, silme yok; kendini kesme > %0.5 ise ret);
  4. pymeshfix tam onarım.
  Hiçbiri geçmezse parça **orijinal hâliyle, dolu** basılır (hollow/delik yok) ve rapor nedenini yazar.
  `hollow.to_manifold(repair=True)` da aynı kontrolü yapar. `REPAIR_VERSION=2` eski onarılmış önbellekleri yeniler.
- Torso: 1 büyük gövde, 17 açık halka (biri kapatılamıyor), üçgenlerin %1.7'si kendini kesiyor → doldurma %21
  uydurma yüzey ekliyor, pymeshfix %19.5 siliyor → artık onarılmıyor, orijinal basılıyor.
- Ders: "geçerli mesh" ≠ "doğru mesh". Şekli değiştiren her otomatik işlem orijinalle karşılaştırılmalı.
- Sonraki adım (onarılamayan parçayı yine de hollow etmek): genelleştirilmiş sarım sayısıyla (açık/kendini kesen
  mesh'te de güvenilir iç/dış) dar bantlı voksel yeniden örnekleme — CHITUBOX'un yaptığına benzer. 150 mm parçada
  0.2 mm ızgara ~1 GB; bellek bütçesine sığdırmak gerekir.

## 6c. Deneme turu: daha önce denenmemiş kitler (2026-10-04)

Araç: `tools/trial_kits.py <yaml>` — her kiti ayrı süreçte `prep` eder; süre, tepe bellek, montaj, plate, onarım,
uyarılar `tools/trial_out/<zaman>.md` özetine yazılır. Bulunan sorunlar (hepsi genel düzeltme + test):

| Kit | Sorun | Düzeltme |
|---|---|---|
| OUISTCRAM Chimchar | sıfır alanlı üçgenler birleşme düzlemi aramasında çöküyordu | `joints._dominant_plane` sıfır alanlıları saymıyor |
| OUISTCRAM Chimchar | iki dilde isim harfli iki kaide seti: ikincisi figüre hiç değmiyor, sessizce basılıyordu | tam montajda "ana montaja değmeyen ayrı grup" uyarısı; kök figürün grubundan |
| Fides Frieren | indirme sitesinin reklam STL'leri (595–772 mm düz yazı plakaları, adı `Torso.STL`/`Set.stl`!) basılıyor, yukarı ekseni/boyu bozabiliyordu | `assembly.detect_junk_parts` (geometri: düz + figürden geniş/çok gövdeli + seyrek üçgen + figüre değmiyor); `scan` da sayıyor |
| Fides Frieren | kalınlığı sıfır dosya yön seçiminde çöküyordu | `orient_part` iç örneksiz parçayı kaldırıyor |
| ZEZ Hulkbuster | parçalar 0.4–1.2 mm montaj boşluğuyla oturuyor → "hiçbir parçaya temas etmiyor" (yapay örnekte tüm kit "bilinmiyor"a düşüyordu) | tam montajda ≤ 1.25 mm boşluk temas (`GAP_TOL`) |
| 1/12 Griffith | kullanılmayan alternatif `merged_base` (1.9 M üçgen) pymeshfix'te ~30 dk | büyük mesh'te pymeshfix ayrı süreçte, 10 dk sınırı |
| 1/12 Griffith | onarılamayan kafanın hacmi dışbükey zarftan: 37.7 ml (gerçek 8.9) | `io.volume_estimate`: işaretli hacim |
| 1/6 Casca CA3D | dağınık export (ne montajlı ne ortalanmış): "montaj bilinmiyor" ve üst üste duran Leg_L "alternatif" sanılıp **basılmıyordu** | tam montaj tutmazsa varyantlar sadece adla + otomatik montaj; kökün grubu küçük kalırsa büyük alt montajın tohumu kök alınıp yeniden → 9/9 |

| Yhorm | 4.5 M üçgenli pelerin, 3.4 M etek "onarılamadı" → dolu basılacaktı; delik doldurma aslında kayıpsız ama 4 yüzlü (iki yüzeyin değdiği) kenarlar yüzünden trimesh "su geçirmez değil" diyordu | kapalılık boolean motoruna göre (`io.closed`); Weapon 39 sn'de onarılıyor (eskiden 10 dk sınırı) |
| Chimchar | harf parçasında marching cubes geçersiz iç kabuk → çöküş | `hollow_part`: kavite eşiğine ulaşamayacak parçada hollow denenmiyor; geçersiz kabuk → dolu |
| Frieren | 1:1'de sandık parçaları M7'ye sığmıyor: plate'e konmuyor, dosyası da yazılmıyordu, kit düzeyinde uyarı yoktu | `parts/` altına yine yazılır; "en çok %X'e küçültün (--scale/--height)" önerisi; yön aramasına sınır kutusu yönleri |
| Frieren, Astarion | yukarı ekseni yanlış (sandık her yüzü düz; kanepe ayakları kıvrımlı): düz taban kuralı yetmiyor | `tipping_angles`: montajın o yönde devrilmeden durma açısı; düz taban ancak dengedeyse kabul |
| Arlecchino | Torso / TorsoForWings alternatifinde kanatsız gövde seçilip kanatlar da basılıyordu (kanat gövdeye 7 mm gömülü) | tam montajda grup dışı parça bir üyeye oturup diğerine gömülüyorsa oturduğu üye seçilir (`_fit_preferences`) |
| Jin Sakai | ölçek referansı T-pozunda insan mankeni (prizma değil): basılıyor, yukarı eksenini saptırıyordu | adı referans + figüre değmiyor → çözümden önce ayrılır |
| Chimchar | ikinci kaide seti alt-alt klasörde, --exclude ile çıkarılamıyordu | --exclude yoldaki her klasör adını tanır; uyarı hazır komutu önerir |
| Maomao & Jinshi | sahnenin içinden geçen reklam plakaları tesadüfen parçalara "değiyor" sayılıp ayrılmıyordu | kalınlıksız ya da figürden geniş seyrek plaka temas kontrolsüz reklam |
| Maomao & Jinshi | Base / Jinshi / Maomao klasörleri ayrı koordinatlarda: farklı klasörlerin parçaları "üst üste = alternatif" sanılıp Maomao'nun eteği gibi parçalar basılmıyordu | `separate_frame_folders`: diğer klasörlere neredeyse hiç değmeyen klasör → klasörler arası geometrik alternatif yok + "klasörleri ayrı hazırlayın" uyarısı |

Kullanıcı sorusu (2026-10-04): "pim ve yuvalar aynı boyutta olduğu için mi karıştırıyor?" — hayır; bu kitler dosyada
montajlı geliyor, sorun hangi parçaların birlikte olduğuna karar vermekteydi. Üç genel mekanizma eklendi:

1. **Sürüm takımları** (`detect_version_sets`): ad sözcükleri (split / merge(d) / bust / full / onepart /
   multipart) en az iki farklı takım oluşturuyorsa takımlar birbirinin alternatifi; `--variant` ile seçilen, yoksa en
   çok parçalı takım basılır. Atsu: Split (30) seçili, BUST (2) ve Merge (1) basılmıyor. Takımlar dosyada farklı
   yerlerde durduğu için (bölünmüş montajlı, diğerleri orijinde) üst üste binme ölçüsü bunu yakalayamıyordu.
2. **Ayrı koordinatlı klasörler** (`frame_clusters` + `_merge_clusters`): klasörlerin sıfırlanmış eksenleri farklıysa
   (Ichigo) ya da iki klasör arasında iç içe geçme temastan çok büyükse (Maomao: Jinshi ↔ Maomao 2.6 bin mm² temas,
   37 bin mm² iç içe) her küme kendi başına çözülür (kendi modu: Ichigo kaidesi otomatik, figürü kısmi) ve yan yana
   konur. Kümeleri birbirine kenetlemeye bilerek çalışılmadı: figür kaidenin düz üstünde yanlış yere oturursa o
   bölge "gizli" sayılır ve görünür yüzeye delik açılırdı.
3. **Orijinde unutulmuş parçalar** (`misplaced_parts` + `place_onto`): montajlı kitte orijine yakın duran ve ya
   hiçbir parçaya değmeyen ya da başka parçalara derin gömülen / orijinde başka parçalarla yığın oluşturan parça,
   alternatif aramasına girmez; montaj yerinde sabitken (`auto_assemble(fixed=…)`) ek yüzeyi / kenetlenmeyle
   yerleştirilir. Atsu: Torso pelerinin içine (3048 mm²), zemin plakasının yarısı diğer yarısının yanına (4611 mm²),
   kılıç kemeri. Orijindeki büyük parça (montajın ≥ %30'u: Maomao Base_No_Fence 785 ml) yerleştirilmez, birleşik
   alternatif sayılır. Ayrıca adı "Alternative/Alt" olup montaja gömülen parça (Alternative_Base) alternatif.
   Sanji'deki sigara (ortalanmış figürün simetri ekseninde, yukarıda) orijinde sayılmaz: orijin noktası kutuya
   yakın olmalı.

Çözülmeyenler (sonraki iş): Maomao Jinshi\Bun, Atsu Sand_Sword_2 / Sword_Belt_2 orijinden yerleştirilemiyor (ayrı
basılıyor). (Yhorm torso ve ayrı koordinatlı kümelerin yeri §6d'de çözüldü.)

## 6d. Güvenli yön alternatifleri, mikro onarım, küme oturtma, reçine, gece kuyruğu (2026-10-04)

- **Peel/vantuz güvenliği**: süre/plate için seçilebilen alternatif yön en iyi yönden fazla deliksiz vantuz çanağı
  bırakamaz ve en büyük katman kesitini en çok MAX_PEEL_GROWTH = 1.5 katına çıkarabilir. Deliklenemeyen çanak başına
  puan cezası (UNVENTED_PENALTY 0.6): Lion kaidesi 6 → 4 deliksiz çanak. Rango pelerini (tam dilimlemeyle): en iyi
  yön 73 mm / 4.7 cm²; eskiden seçilen 41 mm'lik yön 10.5 cm², aşağı bakan düz yüzey %1 → %4. Şimdi en alçak izinli
  alternatif 59 mm / 6.6 cm².
- **Ölçüm hatası dersi**: teşhis betiği katman histogramında ızgaraya hizalı yönlerde (z−zmin)/g ≈ tam sayı çıkınca
  kayan nokta 1.9999→1 yuvarlamasıyla iki katmanı tek bölmeye topluyor, kesiti ~2 kat gösteriyordu (1292 ↔ gerçek
  659 mm²). Uygulamanın tahmini tam dilimlemeyle %2 içinde tutarlı; bölmeler artık +0.5 kaydırılıyor
  (orient.max_layer_area). Bir ölçüm "uygulama yanlış" diyorsa önce ölçümü bağımsız bir yöntemle (tam dilimleme)
  doğrula.
- **Mikro onarım** (io.micro_repair): Yhorm Left_Shoulder_24 (878 k üçgen) 84 açık kenar / 25 döngü (hepsi ≤ 1 mm),
  2 adet 3 yüzlü kenar; Torso_24 (2.7 M) 352 açık kenar / 106 döngü, 10+1 tek sayılı çok yüzlü kenar. 3 yüzlü
  "kanat" PyTMesh'in sınır takibini bozup delik doldurmayı engelliyor, trimesh fill_holes 4'ten çok köşeli deliği
  kapatmıyor, pymeshfix tam onarım şekli bozuyor (%2.3) ya da 10 dk'yı aşıyordu. Tek sayılı kenarlara değen yüzleri
  silip PyTMesh ile doldurmak: omuz 24 sn, torso 75 sn, ikisinde de kaybolan/eklenen %0.0.
- **Küme oturtma** (assembly._seat_clusters): ayrı koordinatlı klasör kümeleri arasında kullanıcı bağlantısı
  (--join KAİDE:AYAK) figür kümesini rijit olarak kaideye oturtur. İki ders: (1) kenetlenme sadece çapaya bakınca
  tepeler çapanın komşusuna gömülen yerlerde toplanıyordu → komşuların içi kenetlenme ızgarasında ceza
  (assembly_grid avoid=); (2) düz yan duvar ayaktan çok temas verir (247 ↔ 200 mm²) → kümenin en alt noktası
  çapanın yüksekliğinin ortasından aşağı inemez (fft_dock min_dir yarı uzay maskesi).
- **Reçine/maliyet** (resin.py) ve **gece kuyruğu** (batch.py, GUI Kuyruk sekmesi): fiyatlar ve kuyruk kullanıcı
  tercihlerinde (~/.slaprep_prefs.json); kuyruk düşük öncelikli süreçlerle sırayla çalışır.

Doğru çıkanlar (önizleme + stüdyo görseliyle kontrol): Lion El Johnson (tam, kasklı sürüm seçili, çıplak kafa
alternatif), Griffith (bölünmüş kaide + bölünmüş pelerin seçili; birleşik sürümler alternatif), 1/6 Casca (otomatik
9/9), Astarion (kanepe + figür), Frieren (sandık + figür; reklam plakaları ayrıldı).

## 6e. Destek yeri ve süre ayarı (2026-10-07)

- **Destek yeri** (kullanıcı isteği: destekler birleşme yerine, sırta, alta gelsin). Eskiden yönlendirme aşağı bakan
  yüzeyleri ikiye ayırıyordu: gizli (birleşme + kaide altı) / görünür — sırtla yüz eşit sayılıyordu. Şimdi yüz başına
  görünürlük ağırlığı (joints.visibility_weights): gizli 0, sırt 0.2, yan 0.6, ön 1.0; alttan bakan yüzler ×(1+0.65·n·up).
  Destek isteği kademeli (orient.support_need: nz −0.55 → −0.85). Aday yönlere "sırt aşağı" ve "alt aşağı" (+35° eğik
  4'er) eklendi. Katsayı taraması (scans/diag_support.py --w, delik terimleri hariç):

  | kit | ön/yan desteklenen alan eski → W=12 | toplam baskı yüksekliği |
  |---|---|---|
  | Rango (18 parça) | 1946 → 1079 mm² (−%45) | 871 → 860 mm |
  | Catwoman (16) | 2274 → 649 mm² (−%71) | 861 → 862 mm |

  W=4'te ağırlıklar küçüldüğü için diğer terimler ağır basıyor, toplam görünür destek artıyordu (Rango sol kol %5 →
  %37); W=20'de parçalar uzamaya başlıyor. Vantuz cezası 0.6 → 2.0 (görünürlük puanı deliksiz çanağı kabul ettirmesin);
  plate/süre alternatifi görünür desteği en çok 1.25 katına çıkarabilir.
- **Ön yön kuralı**: Z yukarı → figür −Y'ye, Y yukarı → +Z'ye bakar (10 kitte önizleme doğru); yukarı ekseni eksi olan
  kitlerde (Atsu, Griffith −Y) ön ters: 'önden' önizleme ve fotoğraf karşılaştırmasının 0° açısı sırtı gösteriyordu
  (180° görünüm ön). Elle: --front-axis.
- **Süre ayarı** (timecal): Rango Bottle ayrı plate 82.7 mm gerçek 211 dk; model 6.5 sn/katman + 15 dk ile 194 dk
  diyordu (%8 kısa) → 7.11 sn/katman. Tek ölçümde ek süre sabit; yükseklikleri farklı ≥2 ölçümde ikisi birlikte.

## 7. Yol haritası (sonraki adımlar)
0. **Otomatik montaj**: pim/yuva prizmalarından eşleştirme (düz ek bulunamayan yerler), dönmüş parçalar için
   rijit (6 serbestlik) eşleştirme.
1. **Destek-delik etkileşimi**: CHITUBOX auto-support delik ağzına destek koyabiliyor. Gizli yüz plate'e baktığı
   için delikler destek bölgesinde kalıyor. Delik çevresinde destek yoğunluğunu tahmin edip ağızları "destek
   gölgesi"nden uzak seçmek.
2. **Deliklenemeyen çanaklar** (5 kitte toplam 9 tane): yönü sadece bu çanak için yeniden aramak (şimdi skor
   teriminin bir parçası) ya da kullanıcıya "bu çanak için görünür ama girintili bir yer" önermek
   (--allow-visible-holes'un hedefli hâli).
3. **Plate'te yükseklik dengeleme**: aynı plate sayısında uzun parçaları aynı plate'e toplamak şu an sadece
   sıralama seçimiyle oluyor; açık bir "yükseklik kümeleme" ek %5–10 süre kazandırabilir.
4. **Montaj çözücü hızı**: chiro'da (31 parça, 178 çift) montaj 5 dk sürüyor; aday çift taraması paralelleştirilebilir
   ya da kaba tarama daha az noktayla yapılabilir.
5. **Kısmi plate'ler arası denge**: son plate'te az parça kalıyorsa (Rango: 3. plate) parçaları
   plate'lere eşit yükseklikte dağıtmak.
