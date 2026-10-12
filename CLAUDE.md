# SLAPrep — Claude Code kullanım notları

Kullanıcı Türkçe konuşur. SLA figür kitlerini (bölünmüş STL'ler) CHITUBOX öncesi hazırlamak için bu aracı kullanır.
Kullanıcının isteğini `slaprep` komutuna çevir, çalıştır, sonra report.html'deki özeti ve uyarıları kısaca Türkçe aktar.

Çalıştırma (bu klasörden): `.venv\Scripts\slaprep.exe <komut> ...` (PowerShell'de `$env:PYTHONIOENCODING="utf-8"` ayarla).
Makinece okunur sonuçlar: `<klasör>\_slaprep\analysis.json` (analyze) ve `prep.json` (prep) — özet için bunları oku.
Raporlar: `analysis.html` (analyze), `report.html` (prep).

Örnek çeviriler:
- "Rango'yu 15 cm yap" → slaprep prep "<klasör>" --height 150
- "kaide hariç figür 12 cm olsun" → --height 120 --height-mode figure
- "V2 kafayı kullan" → --variant Head_V2
- "duvara asılan versiyonu" → --wall-mount (+ varsa askı parçaları: --variant "*_Key"); delikler yine sadece
  birleşme yüzeylerine (arka yüze delik için ayrıca --holes-on-wall-back)
- "kaideyi kullanma" / "şu klasörü alma" → --exclude Base (klasör adı, parça adı veya * deseni)
- "birleşik tabanı kullan" (merged/split seçenekli kitler) → --variant base_merged
- "duvarı 1.5 yap, delikler 2.5 mm olsun" → --wall 1.5 --hole 2.5 (sabit çap; verilmezse çap 3–5 mm otomatik)
- "delikleri büyüt" → --hole-max 6 ; "her kaviteye en az 4 delik" → --holes 4
- "plate'leri klasörlere ayırma" → --no-plates-by-folder (birden çok alt klasör varsa ayırma varsayılan)
- "sadece plate'e diz / hollow yapma" → --steps plates (parçalar dolu); "sadece hollow + delik, plate'e dizme" →
  --steps hollow (parts/ altında orijinal duruşta, plate yok). Uygulamada sol üstteki İşlemler kartı (iki onay
  kutusu: Hollow + delik, Yönlendir + plate'e diz).
- "şu parça dolu olsun" → --solid Ad ; "şu parça ayrı plate'te olsun / şeffaf reçineyle basacağım" →
  --separate Ad (plates\ayrı\ altında kendi plate'leri). Şeffaf reçine parçası için ikisi birlikte:
  --solid Bottle --separate Bottle (hollow şeffaf parçada iç boşluk ve delikler dışarıdan görünür). Uygulamada:
  Parçalar sekmesi → satırdaki 'Ayrı plate' kutusu ve 'Doluluk' hücresi (ya da parçaları seçip üstteki seçim
  çubuğu).
- "en az plate olsun" → --plate-goal plates ; varsayılan --plate-goal time (en kısa toplam baskı süresi: uzun
  parçalar aynı plate'te). "katman süresi 8 sn / katman 0.03" → --layer-time 8 --layer-height 0.03
  (sadece süre tahmini ve yerleşim kararı için; dilimleme CHITUBOX'ta)
- "yukarı ekseni yanlış" (önizlemede figür yan/ters) → --up-axis y / z / -y ...
- "önden görünüm figürün sırtını gösteriyor" / "destekler yüzüne geliyor" → --front-axis z / -y ... (figürün baktığı
  yön; uygulamada Montaj kartı → Ön yönü). Destek yeri: yönlendirme, aşağı bakan (destek isteyen) yüzeyleri görünürlükle
  ağırlıklandırır (joints.visibility_weights: birleşme/kaide altı 0, sırt 0.2, yan 0.6, ön 1.0, alttan bakan azaltılmış;
  orient.SUPPORT_W). Varsayılan ön: Z yukarı → −Y, Y yukarı → +Z, yukarı eksi ise ters (render.default_views).
- "bu plate gerçekte 3 sa 31 dk sürdü" → süre ayarı ölçümü (timecal.add_point; uygulamada Plate'ler → Gerçek süre).
  Ölçümler ~/.slaprep_prefs.json "time_calibration"; CLI --layer-time verilmezse ayarlı katman süresi kullanılır.
  İlk ölçüm 2026-10-07: Rango Bottle ayrı plate 82.7 mm → 211 dk (katman 6.5 → 7.11 sn, ek 15 dk sabit).
- Montaj bulunamayan kitte "30 cm yap" → --height 300 --orig-height <ürün sayfasındaki boy mm> (kullanıcıya sor)
- Parçalar orijine ortalanmışsa araç otomatik montaj dener (autoassemble.py); yetersizse "bilinmiyor"a düşer.
  Kısmen başarılıysa (hacmin ≥ %50'si) boy ana montajdan ölçülür; "yeri bulunamadı" uyarısındaki gruplar boya
  katılmaz — kullanıcıya söyle (figürün üstüne taşan bir grupsa gerçek boy daha fazla olur).
- "şu parça yanlış yerde" (otomatik/kısmi montaj) → --detach Parça ; "kol gövdeye takılıyor" → --join Gövde:Kol
  (ÇAPA:PARÇA, otomatik montajda ve ayrı koordinatlı klasör kümeleri arasında); "el yanlış bileğe gitti" →
  --join Gövde:El:2 (2. aday konum);
  "düzeltmeleri sıfırla" → --clear-fixes. Düzeltmeler slaprep.yaml'a kaydedilir. Uygulamada aynısı: Montaj
  kontrolü sekmesi (Başka konum dene düğmesi; "Fotoğrafla karşılaştır" ile stüdyo görseliyle yan yana bakılır).
- Ölçek referans parçası (HEIGHT_PILLAR gibi düz çubuk) otomatik basılmaz; isterse --print-reference.
- "elleri de hollow et" → --hollow Left_Hand,Right_Hand (eşik uyarısı çıkarsa kullanıcıya söyle)
- "sadece kafayla şapkayı hazırla" → --only Head_V1,Hat
- "bu kit ne kadar büyük, kaç parça?" → slaprep analyze "<klasör>"
- "logo/reklam dosyalarını at" → --exclude lab,pack (dosya adları, uzantısız)
- "ayarları sıfırla" → --reset (kayıtlı slaprep.yaml yok sayılır)
- "klasördeki kitleri tara" → slaprep scan "<kök klasör>" (sadece bbox okur, hızlı; montaj durumuna göre sınıflandırır)

Masaüstü uygulaması: `.venv\Scripts\slaprep-gui.exe` (masaüstü kısayolu "SLAPrep"); kurulum/taşıma: `install.bat`.
Arayüz: gui.py (yerleşim + mantık) ve ui_theme.py (renk tokenları C, PIL ile çizilen yuvarlak öğeler, Section /
Segmented / TabView / ScrollFrame / ThinProgress bileşenleri; ek kütüphane yok). Görsel kontrol için ekran görüntüsü
ALMA: `python -m scans.snap_gui çıktı.png [kit] [--tab N] [--select A,B] [--ops plates|hollow|none] [--log]`
pencereyi ekran dışında açıp yalnız kendisini PNG'ye çizer (açılır menüleri tetikleme: Windows'ta modal döngüde
takılır). ttk image öğesi resmin ORTASINI DÖŞER: esnetilen her resim Theme._element(center=…) ile büyük ortalı
üretilmeli (2026-10-05: 4 px ortalı kart resmi pencereyi açılışta 20+ sn dondurdu, "yanıt vermiyor"). Arayüz
değişikliğinden sonra tepkiyi ölç: ilk çizim ~1 sn, sekme geçişi < 0.1 sn olmalı.
Kullanıcı kılavuzu: README.md. Ar-Ge ölçümleri ve yol haritası: ARGE.md. Kıyaslama araçları: tools\bench_layout.py.

Delik yeri kuralı (kullanıcı tercihi): delikler varsayılan olarak SADECE birleşme yüzeylerine açılır (montajda komşu
parça kapatır). Kaide altı / duvar arkası dış yüzeydir — kullanıcı baskı sonrası delik tıkamak istemiyor; bunlara
sadece açıkça isterse --holes-on-bottom / --holes-on-wall-back ile delik aç.

Geliştirme kuralı (kullanıcı isteği: uygulama sürdürülebilir olsun, sorunları elle çözme):
- Bir kitte sorun çıkınca kitin kendisini elle düzeltme; sorunun genel nedenini uygulamada düzelt. Kit adına özel
  kod yazma (adlar sadece yorumlarda örnek olabilir).
- Montaj/varyant/otomatik montaj kodunu değiştirdikten sonra: `pytest tests -m "not rango"` ve
  `tools\regress.py` (en az --quick; montaj algoritması değiştiyse tamamı). Kalan kit varsa nedenini çöz.
- Yeni mekanizmaya tests/ altında yapay veriyle hızlı test ekle; yeni doğrulanan kiti tools\regress_kits.yaml'a ekle.
- Tek seferlik teşhis betikleri scans/ altında kalır (uygulamanın parçası değil).

Bellek: makinede 15 GB RAM, sayfa dosyası E:'de. Parça yükleme işçisi ≈ STL boyutu × 40 bellek ister (onarım +
sapma kontrolü; 126 MB → 4.8 GB). io._run_load_jobs paralelliği boş belleğe göre ayarlar, işçiler parça başına
kapanır. Kullanıcı "yer yok" hatası görürse önce bellek (sayfa dosyası) sorunu olduğunu düşün, disk değil.

Kurallar:
- Orijinal STL'lere asla dokunma, çıktı her zaman <klasör>/_slaprep/ altında.
- Görünür yüzeye delik açmayı kullanıcı açıkça istemedikçe --allow-visible-holes kullanma.
- Rapordaki "dolu bırakıldı" ve "montaj bilinmiyor" uyarılarını mutlaka kullanıcıya söyle.
- "ön-destekli (pre-supported)" uyarısı çıkarsa: desteksiz sürümü kullanmasını öner (destekli modeller hollow için uygun değil).
- Kullanıcı tüm `STL Modeller` klasörünü taramayı istemiyor; sadece adı verilen kitlerle çalış.
- Kit klasörleri genelde: E:\3d\STL Modeller\<kit adı>\
- CHITUBOX çoklu STL içe aktarma davranışı: (Aşama 5'te kullanıcı testinden sonra buraya yaz)

Bilinen kit davranışları:
- Resmi stüdyo kitleri (CA3D, OXO3D, Lucas Perez, ZEZ...) genelde tam montajlı gelir; parçalarda küçük kusurlar
  (açık kenar, kırıntı gövdeler) olur, araç otomatik onarır (raporda "onarıldı" yazar).
- Plate'e dizilmiş kitler (ör. "1-9scale Casca") montaj doğrulamasından geçemez → "montaj bilinmiyor":
  --height yerine --scale kullanılmalı.
- Zenith_Rango_Repaired: Z ekseni parça başına sıfırlanmış; araç Z kaymalarını çözer (Original ile ±0.4 mm doğrulandı).
- chiro (Haku + Chihiro): 3 alt klasör (Base, Chihiro, Dragon) aynı model. Duvar sürümü:
  `--wall-mount --exclude Base --variant "*_Key" --up-axis y --height 300` (Base içinde kopya Base2 ve 2 m uzakta
  kopuk Base13 var). Kaide olmadan yukarı ekseni otomatik −Y bulunuyor, --up-axis y gerekir.
- 1,12 Scale Guts CA3D: base_merged (dolu, birleşik) ↔ base_part_1/2 + canavar + kılıç (bölünmüş, altı oyuk)
  alternatif; varsayılan bölünmüş set.
- Reklam/filigran STL'leri (indirme sitesinin düz yazı plakaları: lab 595×83×17 mm, pack 772×265×15 mm,
  Miniatures 312×71×0 mm; Frieren'de adları Torso.STL / Set.stl!) assembly.detect_junk_parts ile geometriden
  ayrılır (montaja/boya/baskıya katılmaz, raporda "Reklam/filigran dosyası gibi…" uyarısı). Kullanıcıya söyle;
  yanlışsa --print-reference. Walter White (Zenith), Maomao dioraması, Frieren klasörlerinde var.
- Montaj boşluğu: tam montajlı kitte parçalar arası ≤ 1.25 mm boşluk temas sayılır (GAP_TOL; ZEZ Hulkbuster
  parçaları 0.4–1.2 mm boşlukla oturuyor, eskiden "hiçbir parçaya temas etmiyor" deniyordu).
- ZEZ Hulkbuster Base_2.stl: Magics başlıklı ('COLOR=… MATERIAL=…') ikili STL, başlıktaki üçgen sayısı yanlış
  (937132 ↔ dosya 937592). trimesh boş okuyordu; io.load_mesh_robust dosya boyutuna göre okur (CHITUBOX gibi).
  "X yüklenemedi / No points given / 0 üçgen" görürsen önce başlık–boyut uyuşmazlığına bak.
- ZEZ Hulkbuster Hulk_buster_Torso.stl: açık + kendini kesen mesh; her onarım yüzeyin %19–29'unu siliyor ya da
  %21 uydurma yüzey ekliyordu → onarılmaz, orijinal hâliyle DOLU basılır (raporda "Su geçirmez değil…").
  Onarımlar orijinalle karşılaştırılır (io.repair_fidelity); "onarım şekli bozacağı için yapılmadı" uyarısını
  kullanıcıya söyle (parça ağır/dolu çıkar; isterse CHITUBOX'ta kendisi hollow edebilir).
- 1-9 Scale Radahn: her parça tek tek orijine ortalanmış (montaj bilgisi yok) → otomatik montaj 29/32 parça,
  --height çalışır. Kitte iki sürüm var: V2 (varsayılan) = Head_v2 + Torso_v2 ("Miquella\Radahn miquella") +
  Miquella ve 7 saç bloğu Radahn'ın sırtında, boy 261.5 mm; V1 = Head_v1 (yeleli) + Radaam\Torso, Miquella yok,
  boy 245.8 mm → `--exclude Miquella` (Head_v2/Torso_v2 de o klasörde; montaj yeniden çözülür). İkisini birden basmak:
  --print-all-variants. Sword_1..3 ve Miquella'nın minik kolları/eli yerleşmiyor (ayrı basılır, boya etkisi yok).
  Analiz ~10 dk sürer (bir kez, önbelleğe alınır).
- Montaj bilinmiyorsa araç hiçbir parçayı otomatik dışlamaz; ad/boyut benzerliğini sadece "olası alternatif" diye bildirir.
- 1-6scale Casca Berserk CA3D: parçalar dağınık export (ne montajlı ne ortalanmış). Tam montaj tutmayınca otomatik
  montaja düşülür; 9/9 yerleşir, boy ~393 mm. "Dosyalardaki konumlar montajlı değil" uyarısı normaldir.
- OUISTCRAM Chimchar: BASE altında iki kaide seti (CHIMCHAR / OUISTICRAM harfli); ikincisi "ana montaja değmeyen
  ayrı grup" diye bildirilir — kullanıcıya hangisini istediğini sor, diğerini --exclude ile çıkar. MULTI PART ↔
  ONE PART alternatif (varsayılan çok parçalı).
- Deneme turu aracı: `tools\trial_kits.py tools\trial_kits*.yaml` (yeni kitleri toplu prep + özet).
- Fides Frieren: 1:1'de sandık (chest1/chest2) M7'ye sığmaz → rapor "en çok %X'e küçültün" der; parçalar
  parts/ altında ayrıca durur. Yukarı ekseni +Y (denge ölçütü).
- Arlecchino: Torso ↔ TorsoForWings alternatif; kanatlar (WingBase, Upperwing) sadece TorsoForWings'e oturur →
  kanatlı sürüm seçilir. Kanatsız: --variant Torso --exclude WingBase,Upperwing.
- "Yazıcıya sığmayan parçalar" uyarısını mutlaka kullanıcıya söyle (önerilen --scale / --height ile).
- Ghost of Tsushima Jin Sakai 250mm: 250mmreference T-pozunda insan mankeni (ölçek referansı) — adı referans + figüre
  değmiyor → çözümden önce ayrılır, basılmaz. Bölünmüş kaide (7_Base2A/8_Base2B) ↔ birleşik 3_Base alternatif.
- Atsu (Ghost of Yōtei) STL klasörü: üç sürüm (Atsu_Split_* bölünmüş heykel, Atsu_Merge_Body, Atsu_BUST +
  Atsu_BUST_Stand) ad sözcüğünden sürüm takımı olarak tanınır, bölünmüş seçilir (büst için --variant "*BUST*").
  Bölünmüş takımdan 5 parça dosyada orijine yığılmış; 3'ü otomatik yerine oturur, Sand_Sword_2 ve Sword_Belt_2
  ayrı basılır.
- Ichigo (vse_pro_3D): kökten `--exclude Supported1,Supported2,No_Supported3` ile hazırla. Kaide ve figür klasörleri
  farklı export biçiminde → ayrı montajlanıp yan yana konur (kaide otomatik, figür kısmi montaj).
- "Alt klasörler ayrı koordinatlarda export edilmiş" uyarısı: kümeler doğru kurulur ama birbirine göre yerleri
  bilinmez (figürün kaidede nerede durduğu); boy en uzun kümeden. Kullanıcıya söyle. "Figürü kaideye oturt" →
  --join KAİDE_PARÇASI:FİGÜRÜN_AYAĞI (kümeler arası bağlantı: figür kümesi rijit olarak kaidenin ÜSTÜNE oturur —
  kümenin en alt noktası çapanın yüksekliğinin ortasından aşağı inemez; assembly._seat_clusters). Yanlış yerse
  --join ...:2 (Montaj kontrolü → Başka konum dene).
- "Orijinde duran parçalar yerine oturtuldu" uyarısı: önizlemede kontrol ettir (Montaj kontrolü'nde turuncu).
- Yhorm: kaide birleşme yüzeyinde delik yeri olmadığından dolu (820 ml). Torso_24 (2.7 M üçgen) ve Left_Shoulder_24
  eskiden onarılamıyordu (≤ 1 mm delikler + 3 yüzlü "kanat" kenarları); io.micro_repair kayıpsız kapatıyor (torso
  75 sn). REPAIR_VERSION 4: bir sonraki yüklemede yeniden onarılır, hollow edilir — eski çıktıları yeniden hazırlat.
- Yönlendirme: alternatif (plate verimi / süre) yönler en iyi yönden fazla deliksiz vantuz bırakamaz ve en büyük
  katman kesitini en çok 1.5 katına çıkarabilir (orient.select_alternatives, MAX_PEEL_GROWTH). Rango pelerini
  eskiden süre için 41 mm'lik yöne çevriliyordu (kesit 4.7 → 10.5 cm²). Uyarıda yükseklik ve kesit yazar.
  "eğim X°" birleşme (gizli) yüzeyine göredir, plate'e göre değil.
- Reçine/maliyet: fiyatlar kullanıcıya ait (~/.slaprep_prefs.json, prefs.py; testler conftest ile yalıtılır).
  Uygulamada sol kenardaki Reçine kartı; hesap resin.resin_summary (prep.json'daki hazır hacimler + destek payı, "ayrı" plate
  grubu ayrı fiyatla). Gece kuyruğu: Kuyruk sekmesi (batch.py; sırayla, "Düşük öncelik" = BELOW_NORMAL süreç).
  Kuyruk tek kitlik iş sürerken başlatılabilir (iş bitince başlar; aynı kit + aynı argümanlı bekleyen öğe "bitti"
  sayılır). Uygulama tek pencere (gui.serve_instance, 127.0.0.1:47613): ikinci açılış klasörü açık pencereye iletir.
  2026-10-04 gecesi chiro "2) Hazırla" ile çalışırken kuyruğa eklenen chiro + Rango hiç başlamamıştı — bu yüzden.
- Chimchar ikinci dil seti: `--exclude OUISTICRAM` (--exclude artık yoldaki her klasör adını tanır).
- Maomao & Jinshi dioraması: Base / Jinshi / Maomao alt klasörleri ayrı koordinatlarda → üç küme ayrı montajlanır.
  Base_No_Fence / Base_W_Fence_No_Cuts (orijindeki birleşik kaideler) ve Alternative_Base basılmaz (bölünmüş
  Base1..6 seçili). Jinshi\Bun dosyada orijinde, yeri bulunamıyor (ayrı basılır). lab/pack/Miniatures reklam.
