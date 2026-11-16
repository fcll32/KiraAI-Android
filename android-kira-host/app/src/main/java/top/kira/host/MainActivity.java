package top.kira.host;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import android.view.View;
import android.view.ViewGroup;
import android.view.Gravity;
import android.view.WindowInsetsController;
import android.animation.ValueAnimator;
import android.webkit.JavascriptInterface;
import android.text.InputType;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Set;

public final class MainActivity extends Activity {
    private static final String PANEL = "http://127.0.0.1:5267/";
    private static final String NAPCAT_PANEL = "http://127.0.0.1:6099/webui/";
    private static final int CHOOSE_UPLOAD = 41;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refresh = new Runnable() {
        @Override public void run() { render(); handler.postDelayed(this, 500); }
    };
    private SharedPreferences preferences;
    private ShellOnboarding onboarding;
    private LinearLayout stageColumn;
    private Set<String> selectedComponents;
    private boolean legacyNapcatInstalled;
    private WebView web;
    private final WebView[] panelViews = new WebView[2];
    private final PanelDownloads[] panelDownloads = new PanelDownloads[2];
    private final boolean[] panelsLoaded = new boolean[2], panelLoading = new boolean[2];
    private final String[] panelHealth = {"unknown", "unknown"};
    private int navigationGeneration;
    private boolean navigationQueued;
    private final long[] nextPanelAttempt = new long[2];
    private PanelWorkspace workspace;
    private LinearLayout header, dock, branding;
    private TextView brand, caption, localIndicator;
    private boolean compactChrome;
    private ScrollView environment;
    private TextView title, detail, overallText, transferText, transferDetail;
    private TextView workspaceState;
    private LinearLayout environmentCard;
    private FrameLayout environmentFaces;
    private Button environmentFlip;
    private final TextView[] environmentComponents = new TextView[2];
    private int aboutVersionTaps;
    private long lastAboutVersionTap;
    private boolean easterEggOpen;
    private ShellNavigation navigation;
    private ShellDrawer drawer;
    private Button menuButton;
    private final TextView[] componentBadge = new TextView[2];
    private final ArrayList<FrameLayout> componentCards = new ArrayList<>();
    private ProgressBar overallBar, transferBar;
    private Button toggle, prepareStart;
    private final Button[] preparationPanels = new Button[2];
    private final TextView[] componentStatus = new TextView[2];
    private final Button[] componentStart = new Button[2], componentStop = new Button[2], componentRestart = new Button[2], panelButtons = new Button[2];
    private boolean showingEnvironment = true;
    private String selectedPanel;
    /** KiraAI 面板自己的日间/夜间；显示该面板时，标题栏、底栏和系统栏跟着渐变（night：0 日间，1 夜间）。 */
    private boolean panelDark;
    private float night, nightTarget;
    private ValueAnimator nightAnimator;
    private FrameLayout screenFrame;
    private View nightVeil;
    private ShellGlass menuNight;
    private ValueCallback<Uri[]> upload;
    private ApkUpdater updater;
    private KiraBackupManager backups;
    private Toast notice;
    private String noticeText;
    private long nextNoticeAt;
    private static final int INK = ShellTheme.INK;
    private static final int MUTED = ShellTheme.MUTED;
    private static final int BLUE = ShellTheme.ACCENT;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        preferences = getSharedPreferences("linux_host", Context.MODE_PRIVATE);
        selectedComponents = ComponentSelection.load(this);
        legacyNapcatInstalled = ComponentSelection.legacyNapcat(this, true);
        updater = new ApkUpdater(this);
        updater.cleanStaleDownloads();
        backups = new KiraBackupManager(this, this::confirmKiraBackup, () -> invoke("kira", "restore"));
        buildLayout();
        // 首次启动先看新手开始页；环境准备照常在下面自动开始，引导页关掉后就能看到进度。
        if (onboarding.firstRun()) onboarding.show();
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 11);
        if (!RuntimeStatus.automaticStartPaused(this) && !RuntimeStatus.alive(this)
                && canStartSelected(RuntimeStatus.read(this))) sendComponentCommand("all", "start", true);
    }

    /** 引导之后光标要搭出来的主界面：顶栏三块、首页每一块、底栏。看不见的由 ShellOnboarding 跳过。 */
    private java.util.List<View> buildTargets() {
        java.util.List<View> targets=new java.util.ArrayList<>();
        targets.add(menuButton); targets.add(branding); targets.add(localIndicator);
        if (showingEnvironment && stageColumn!=null)
            for (int index=0;index<stageColumn.getChildCount();index++) targets.add(stageColumn.getChildAt(index));
        targets.add(navigation);
        return targets;
    }

    private void buildLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.TRANSPARENT);
        header = new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(20), dp(8), dp(20), dp(4));
        header.setBackgroundColor(Color.TRANSPARENT); header.setClipToPadding(false);
        menuButton = iconButton(ShellIcons.MENU,"打开菜单", view -> { if (drawer.isOpen()) drawer.close(); else drawer.open(); });
        menuButton.setTextSize(22); menuButton.setGravity(Gravity.CENTER);
        menuButton.setPadding(0,0,0,0); menuButton.setTextColor(INK);
        menuButton.setContentDescription("打开菜单"); menuButton.setTooltipText("菜单");
        menuNight = ShellTheme.nightFrost(this, 15); menuNight.setAlpha(0);
        menuButton.setBackground(new LayerDrawable(new Drawable[]{ShellTheme.frost(this, 15), menuNight})); ShellTheme.lift(menuButton, 3);
        header.addView(menuButton,new LinearLayout.LayoutParams(dp(44),dp(44)));
        branding = new LinearLayout(this); branding.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams brandParams = new LinearLayout.LayoutParams(0,-2,1); brandParams.leftMargin = dp(14);
        header.addView(branding,brandParams);
        brand = label(branding,"KiraAI",16); brand.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL)); brand.setPadding(0,0,0,0);
        caption = label(branding,"本机组件管理",11); caption.setTextColor(MUTED); caption.setPadding(0,dp(2),0,0);
        localIndicator = new TextView(this); localIndicator.setText("● 未运行"); localIndicator.setTextSize(11);
        localIndicator.setTextColor(ShellTheme.INK); localIndicator.setPadding(dp(10),dp(7),dp(10),dp(7));
        localIndicator.setBackground(ShellTheme.surface(this,0xFFFDF6FA,18,0xFFFFFFFF));
        header.addView(localIndicator); root.addView(header,new LinearLayout.LayoutParams(-1,-2));

        workspace = new PanelWorkspace(this); root.addView(workspace,new LinearLayout.LayoutParams(-1,0,1));
        for (int index=0;index<2;index++) {
            panelViews[index]=new WebView(this); panelViews[index].setBackgroundColor(ShellTheme.PAGE);
            panelDownloads[index]=new PanelDownloads(this,panelViews[index],43+index); configureWebView(index);
        }
        web=panelViews[0];
        environment=new ScrollView(this); environment.setClipToPadding(false); environment.setVerticalScrollBarEnabled(false);
        LinearLayout preparation=new LinearLayout(this); preparation.setOrientation(LinearLayout.VERTICAL); stageColumn=preparation;
        preparation.setPadding(dp(20),dp(14),dp(20),dp(28));
        // 卡片的柔光阴影要越过左右留白和叠卡自身的边界；按内边距或子视图边界裁剪会把光晕切成直边。
        preparation.setClipToPadding(false); preparation.setClipChildren(false);
        TextView kicker=ShellCards.text(this,"本机工作台",11,ShellTheme.MUTED); kicker.setLetterSpacing(.08f);
        preparation.addView(kicker,new LinearLayout.LayoutParams(-1,-2));
        TextView pageHeadline=ShellCards.text(this,"KiraAI 手机版",23,ShellTheme.INK);
        pageHeadline.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        LinearLayout.LayoutParams headlineParams=new LinearLayout.LayoutParams(-1,-2); headlineParams.topMargin=dp(7); preparation.addView(pageHeadline,headlineParams);
        TextView pageLede=ShellCards.text(this,"在手机上运行 KiraAI 与 NapCat。",13,ShellTheme.MUTED);
        pageLede.setLineSpacing(dp(3),1f); LinearLayout.LayoutParams ledeParams=new LinearLayout.LayoutParams(-1,-2); ledeParams.topMargin=dp(4); preparation.addView(pageLede,ledeParams);
        TextView guideLink=ShellCards.text(this,"菜单里可管理令牌、备份、更新和日志  ›",12,ShellTheme.ACCENT);
        LinearLayout.LayoutParams guideParams=new LinearLayout.LayoutParams(-1,-2); guideParams.topMargin=dp(8); preparation.addView(guideLink,guideParams);
        guideLink.setMinHeight(dp(44)); guideLink.setGravity(Gravity.CENTER_VERTICAL);
        guideLink.setOnClickListener(view -> drawer.open()); ShellDialogs.pressFeedback(guideLink);
        LinearLayout hero=buildConnectionHero();
        LinearLayout.LayoutParams heroParams=new LinearLayout.LayoutParams(-1,-2); heroParams.topMargin=dp(18); preparation.addView(hero,heroParams);
        TextView setupHeading=ShellCards.text(this,"连接准备",17,ShellTheme.INK); setupHeading.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        LinearLayout.LayoutParams setupHeadingParams=new LinearLayout.LayoutParams(-1,-2); setupHeadingParams.topMargin=dp(24); setupHeadingParams.bottomMargin=dp(8); preparation.addView(setupHeading,setupHeadingParams);
        preparation.addView(buildSetupDeck(),new LinearLayout.LayoutParams(-1,-2));
        TextView setupHint=ShellCards.text(this,"轻点叠卡展开，选择对应操作；再次轻点可收起。",12,ShellTheme.MUTED);
        LinearLayout.LayoutParams hintParams=new LinearLayout.LayoutParams(-1,-2); hintParams.topMargin=dp(8); preparation.addView(setupHint,hintParams);
        TextView moreHeading=ShellCards.text(this,"更多功能",17,ShellTheme.INK); moreHeading.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        LinearLayout.LayoutParams moreHeadingParams=new LinearLayout.LayoutParams(-1,-2); moreHeadingParams.topMargin=dp(24); moreHeadingParams.bottomMargin=dp(8); preparation.addView(moreHeading,moreHeadingParams);
        preparation.addView(buildFeatureStack(),new LinearLayout.LayoutParams(-1,-2));
        TextView componentHeading=ShellCards.text(this,"运行组件",17,ShellTheme.INK); componentHeading.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        LinearLayout.LayoutParams componentHeadingParams=new LinearLayout.LayoutParams(-1,-2); componentHeadingParams.topMargin=dp(24); componentHeadingParams.bottomMargin=dp(8); preparation.addView(componentHeading,componentHeadingParams);
        addComponent(preparation,"kira","KiraAI",0); addComponent(preparation,"napcat","NapCat",1);
        TextView footnote=ShellCards.text(this,"只有你允许的操作会触发组件动作。运行期间可随时停止。",12,ShellTheme.MUTED);
        LinearLayout.LayoutParams footParams=new LinearLayout.LayoutParams(-1,-2); footParams.topMargin=dp(6); preparation.addView(footnote,footParams);
        environment.addView(preparation);
        workspace.addPage(environment); workspace.addPage(panelViews[0]); workspace.addPage(panelViews[1]); workspace.showPage(0,false);
        dock=new LinearLayout(this); dock.setGravity(Gravity.CENTER); dock.setPadding(dp(20),dp(8),dp(20),dp(12));
        dock.setBackgroundColor(Color.TRANSPARENT); dock.setClipToPadding(false);
        navigation=new ShellNavigation(this,index -> {
            if (index==0) showEnvironment(); else if (index==1 || index==2) showPanel(index==1 ? "kira" : "napcat");
        });
        dock.addView(navigation,new LinearLayout.LayoutParams(-1,dp(68))); root.addView(dock,new LinearLayout.LayoutParams(-1,-2));
        FrameLayout screen=new FrameLayout(this); screen.setFitsSystemWindows(true); screenFrame=screen;
        // 底色只在系统栏后面露出来（Android 15 起系统栏透明），夜间时跟着变深。
        screen.setBackgroundColor(ShellTheme.DAY_STATUS);
        screen.addView(new ShellBackdrop(this),new FrameLayout.LayoutParams(-1,-1));
        nightVeil=new View(this); nightVeil.setBackgroundColor(ShellTheme.NIGHT_PAGE); nightVeil.setAlpha(0f);
        nightVeil.setVisibility(View.INVISIBLE); nightVeil.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        screen.addView(nightVeil,new FrameLayout.LayoutParams(-1,-1));
        screen.addView(root,new FrameLayout.LayoutParams(-1,-1));
        drawer=new ShellDrawer(this,new ShellDrawer.Listener() {
            @Override public void onAction(int action) { menuAction(action); }
            @Override public void onOpenChanged(boolean open) {
                root.setImportantForAccessibility(open ? View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS : View.IMPORTANT_FOR_ACCESSIBILITY_AUTO);
                menuButton.setSelected(open); menuButton.setContentDescription(open ? "关闭菜单" : "打开菜单");
                if (!open) menuButton.requestFocus();
            }
        });
        screen.addView(drawer,new FrameLayout.LayoutParams(-1,-1)); setContentView(screen);
        onboarding=new ShellOnboarding(this,screen,preferences,this::batterySettings,this::buildTargets,root,drawer);
    }

    /** 双面同时测量；真实进度与操作区保持原位，翻面时不随文字或下载状态伸缩。 */
    private LinearLayout buildConnectionHero() {
        environmentCard = new LinearLayout(this);
        environmentCard.setOrientation(LinearLayout.VERTICAL);
        environmentCard.setPadding(dp(18),dp(16),dp(18),dp(14));
        environmentCard.setBackground(ShellTheme.glass(this,28).tint(ShellTheme.BLOOM_ROSE));
        ShellTheme.lift(environmentCard,10);
        // 按钮的同色光晕要落进卡片留白里，不被内边距和子行边界切成直边。
        environmentCard.setClipToPadding(false); environmentCard.setClipChildren(false);
        environmentFaces = flipFrame();
        LinearLayout front = new LinearLayout(this); front.setOrientation(LinearLayout.VERTICAL);
        LinearLayout heading = new LinearLayout(this); heading.setGravity(Gravity.CENTER_VERTICAL);
        heading.addView(ShellCards.text(this,"环境准备",12,MUTED),new LinearLayout.LayoutParams(0,-2,1));
        workspaceState = ShellCards.text(this,"未运行",11,BLUE);
        workspaceState.setPadding(dp(9),dp(4),dp(9),dp(4));
        workspaceState.setBackground(ShellTheme.surface(this,ShellTheme.ROSE_SOFT,18,Color.TRANSPARENT));
        heading.addView(workspaceState); front.addView(heading);
        LinearLayout main = new LinearLayout(this); main.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout copy = new LinearLayout(this); copy.setOrientation(LinearLayout.VERTICAL);
        title = ShellCards.text(this,"等待启动",18,INK);
        title.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        title.setLines(2); title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        copy.addView(title,new LinearLayout.LayoutParams(-1,stableTextHeight(title,2)));
        detail = ShellCards.text(this,"准备本机环境与运行组件。",12,MUTED);
        detail.setLines(2); detail.setEllipsize(android.text.TextUtils.TruncateAt.END);
        copy.addView(detail,new LinearLayout.LayoutParams(-1,stableTextHeight(detail,2)));
        main.addView(copy,new LinearLayout.LayoutParams(0,-2,1));
        main.addView(ShellIcons.view(this,ShellIcons.HERO),new LinearLayout.LayoutParams(dp(92),dp(90)));
        LinearLayout.LayoutParams mainParams = new LinearLayout.LayoutParams(-1,-2); mainParams.topMargin=dp(8);
        front.addView(main,mainParams);
        LinearLayout back = new LinearLayout(this); back.setOrientation(LinearLayout.VERTICAL); back.setVisibility(View.INVISIBLE);
        TextView backTitle=ShellCards.text(this,"本机运行概览",17,INK);
        backTitle.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL)); back.addView(backTitle);
        TextView device=ShellCards.text(this,"Android "+Build.VERSION.RELEASE+" · 两项服务独立管理",11,MUTED);
        device.setSingleLine(true); device.setEllipsize(android.text.TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams deviceParams=new LinearLayout.LayoutParams(-1,stableTextHeight(device,1)); deviceParams.topMargin=dp(5); back.addView(device,deviceParams);
        for(int i=0;i<2;i++) {
            LinearLayout row=new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(10),dp(4),dp(10),dp(4));
            row.setBackground(ShellTheme.surface(this,i==0?ShellTheme.ROSE_SOFT:ShellTheme.LILAC_SOFT,16,Color.TRANSPARENT));
            row.addView(ShellIcons.view(this,i==0?ShellIcons.CORE:ShellIcons.BRIDGE),new LinearLayout.LayoutParams(dp(36),dp(36)));
            TextView name=ShellCards.text(this,i==0?"KiraAI":"NapCat",13,INK);
            LinearLayout.LayoutParams nameParams=new LinearLayout.LayoutParams(0,-2,1); nameParams.leftMargin=dp(8);row.addView(name,nameParams);
            environmentComponents[i]=ShellCards.text(this,"已停止",11,BLUE);
            environmentComponents[i].setSingleLine(true); row.addView(environmentComponents[i],new LinearLayout.LayoutParams(-2,-2));
            LinearLayout.LayoutParams rowParams=new LinearLayout.LayoutParams(-1,dp(44)); rowParams.topMargin=dp(8); back.addView(row,rowParams);
        }
        environmentFaces.addView(front,new FrameLayout.LayoutParams(-1,-2));
        environmentFaces.addView(back,new FrameLayout.LayoutParams(-1,-2));
        environmentCard.addView(environmentFaces,new LinearLayout.LayoutParams(-1,-2));
        componentCards.add(environmentFaces);
        overallText=ShellCards.text(this,"环境未运行",11,MUTED); overallText.setSingleLine(true);
        LinearLayout.LayoutParams overallParams=new LinearLayout.LayoutParams(-1,stableTextHeight(overallText,1));overallParams.topMargin=dp(10);environmentCard.addView(overallText,overallParams);
        overallBar=bar(environmentCard);
        transferText=ShellCards.text(this,"启动后显示依赖下载与安装进度。",11,MUTED);
        transferText.setSingleLine(true);transferText.setEllipsize(android.text.TextUtils.TruncateAt.END);
        environmentCard.addView(transferText,new LinearLayout.LayoutParams(-1,stableTextHeight(transferText,1)));
        transferDetail=ShellCards.text(this,"",11,ShellTheme.MUTED);transferDetail.setSingleLine(true);transferDetail.setEllipsize(android.text.TextUtils.TruncateAt.END);
        environmentCard.addView(transferDetail,new LinearLayout.LayoutParams(-1,stableTextHeight(transferDetail,1)));
        transferBar=bar(environmentCard);transferBar.setVisibility(View.INVISIBLE);
        toggle=button("启动全部",view->toggleRuntime());styleButton(toggle,true);
        environmentCard.addView(toggle,new LinearLayout.LayoutParams(-1,dp(48)));
        LinearLayout links=new LinearLayout(this);
        environmentFlip=button("运行概览  ↻",view->flipComponentCard(environmentFaces));
        environmentFlip.setTextSize(12);
        Button inspect=button("设备详情  ›",view->showDeviceStatus());inspect.setTextSize(12);
        LinearLayout.LayoutParams flipParams=new LinearLayout.LayoutParams(0,dp(44),1);
        links.addView(environmentFlip,flipParams);
        LinearLayout.LayoutParams inspectParams=new LinearLayout.LayoutParams(0,dp(44),1);inspectParams.leftMargin=dp(8);links.addView(inspect,inspectParams);
        LinearLayout.LayoutParams linksParams=new LinearLayout.LayoutParams(-1,-2);linksParams.topMargin=dp(8);environmentCard.addView(links,linksParams);
        return environmentCard;
    }

    private ShellAccordion buildSetupDeck() {
        ShellAccordion steps = new ShellAccordion(this);
        LinearLayout environment = ShellCards.column(this,16);
        dialogCopy(environment,"查看安装状态，或调整下次启动使用的环境变量。",12,MUTED,0,10);
        addInlineAction(environment,"查看设备状态",this::showDeviceStatus);
        addInlineAction(environment,"编辑环境变量",this::editEnvironment);
        steps.addItem("01","准备运行环境","安装状态与自定义变量",BLUE,environment);
        LinearLayout start = ShellCards.column(this,16);
        dialogCopy(start,"启动本机环境，并依次准备 KiraAI 与 NapCat。各组件也可在下方单独启停。",12,MUTED,0,10);
        prepareStart = addInlineAction(start,"启动全部",this::startRuntime);
        steps.addItem("02","启动运行组件","KiraAI 与 NapCat 独立管理",BLUE,start);
        LinearLayout panels = ShellCards.column(this,16);
        dialogCopy(panels,"组件就绪后，进入各自的官方管理面板。",12,MUTED,0,10);
        preparationPanels[0] = addInlineAction(panels,"打开 KiraAI 面板",() -> showPanel("kira"));
        preparationPanels[1] = addInlineAction(panels,"打开 NapCat 面板",() -> showPanel("napcat"));
        steps.addItem("03","打开官方面板","账号、模型与业务配置",ShellTheme.TEAL,panels);
        return steps;
    }

    private ShellAccordion buildFeatureStack() {
        ShellAccordion features = new ShellAccordion(this);
        LinearLayout device = ShellCards.column(this,16);
        dialogCopy(device,"查看手机、共享环境和两个组件的完整运行状态。",12,MUTED,0,10);
        addInlineAction(device,"查看设备状态",this::showDeviceStatus);
        features.addItem("◎","设备状态","手机与组件运行详情",BLUE,device);
        LinearLayout battery = ShellCards.column(this,16);
        dialogCopy(battery,"检查系统电池优化，调整应用的后台运行权限。",12,MUTED,0,10);
        addInlineAction(battery,"后台与电池设置",this::batterySettings);
        features.addItem("▢","后台与电池","减少后台运行中断",ShellTheme.TEAL,battery);
        LinearLayout logs = ShellCards.column(this,16);
        dialogCopy(logs,"查看系统依赖安装记录和 NapCat 终端日志。",12,MUTED,0,10);
        addInlineAction(logs,"环境安装日志",() -> showLog("environment"));
        addInlineAction(logs,"NapCat 日志",() -> showLog("napcat"));
        features.addItem("≡","安装与运行日志","排查安装失败与连接问题",BLUE,logs);
        return features;
    }

    private Button addInlineAction(LinearLayout parent,String label,Runnable action) {
        Button button=button(label,view -> action.run());
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2); params.topMargin=dp(6);
        parent.addView(button,params);
        return button;
    }

    private void addComponent(LinearLayout parent,String id,String name,int index) {
        FrameLayout component=card(parent,index);
        LinearLayout front=(LinearLayout) component.getChildAt(0);
        if (index == 0) {
            TextView kicker=ShellCards.text(this,"01   /   KIRAAI CORE",10,ShellTheme.ACCENT);
            kicker.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
            LinearLayout.LayoutParams kp=new LinearLayout.LayoutParams(-1,-2); kp.bottomMargin=dp(16);
            front.addView(kicker,kp);
        } else {
            LinearLayout band=new LinearLayout(this); band.setGravity(Gravity.CENTER_VERTICAL);
            TextView kicker=ShellCards.text(this,"02   /   BRIDGE SERVICE",10,ShellTheme.TEAL);
            kicker.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
            band.addView(kicker,new LinearLayout.LayoutParams(0,-2,1));
            TextView edition=ShellCards.text(this,"FORK 版",10,ShellTheme.TEAL);
            edition.setPadding(dp(10),dp(5),dp(10),dp(5));
            edition.setBackground(ShellTheme.surface(this,ShellTheme.LILAC_SOFT,12,Color.TRANSPARENT));
            band.addView(edition);
            LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,-2); bp.bottomMargin=dp(18);
            front.addView(band,bp);
        }
        LinearLayout heading=new LinearLayout(this); heading.setGravity(Gravity.CENTER_VERTICAL);
        heading.addView(ShellIcons.view(this,index==0?ShellIcons.CORE:ShellIcons.BRIDGE),new LinearLayout.LayoutParams(dp(56),dp(56)));
        TextView nameLabel=ShellCards.text(this,name,index==0 ? 22 : 19,INK); nameLabel.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        LinearLayout.LayoutParams np=new LinearLayout.LayoutParams(0,-2,1); np.leftMargin=dp(12); heading.addView(nameLabel,np);
        componentBadge[index]=ShellCards.text(this,"",10,MUTED); componentBadge[index].setPadding(dp(9),dp(6),dp(9),dp(6)); heading.addView(componentBadge[index]);
        front.addView(heading);
        if (index==1) {
            // 原 NapNeko/NapCatQQ 已删库：安装和更新改用社区 fork，卡片正面就把来源讲完整，不用点进弹窗才知道。
            front.addView(forkNotice(),forkNoticeParams());
        }
        componentStatus[index]=label(front,"尚未启动",12); componentStatus[index].setTextColor(MUTED);
        componentStatus[index].setLineSpacing(dp(4),1);
        componentStatus[index].setLines(3); componentStatus[index].setEllipsize(android.text.TextUtils.TruncateAt.END);
        componentStatus[index].setPadding(index==0 ? 0 : dp(13),dp(13),index==0 ? 0 : dp(13),dp(16));
        componentStatus[index].getLayoutParams().height=stableTextHeight(componentStatus[index],3);
        if (index==1) {
            componentStatus[index].setBackground(ShellTheme.surface(this,ShellTheme.LILAC_SOFT,16,Color.TRANSPARENT));
            // 说明面板与上方图标、下方操作条各留一道间隙，三块淡紫不再贴在一起。
            LinearLayout.LayoutParams sp=(LinearLayout.LayoutParams)componentStatus[index].getLayoutParams(); sp.topMargin=dp(10); sp.bottomMargin=dp(8);
        }
        LinearLayout actions=new LinearLayout(this);
        actions.setPadding(dp(5),dp(5),dp(5),dp(5));
        actions.setBackground(ShellTheme.surface(this,index==0 ? ShellTheme.ROSE_SOFT : ShellTheme.LILAC_SOFT,17,Color.TRANSPARENT));
        componentStart[index]=addToolbar(actions,"启动",view -> startComponent(id));
        if (index==1) componentStart[index].setMaxLines(2);
        componentStop[index]=addToolbar(actions,"停止",view -> invoke(id,"stop"));
        componentRestart[index]=addToolbar(actions,"重启",view -> invoke(id,"restart"));
        front.addView(actions,new LinearLayout.LayoutParams(-1,-2));
        panelButtons[index]=button("打开 "+name+" 面板  ↗",view -> showPanel(id));
        if (index==0) ShellTheme.button(panelButtons[index],ShellTheme.ACCENT,true);
        LinearLayout.LayoutParams pp=new LinearLayout.LayoutParams(-1,dp(48)); pp.topMargin=dp(10); front.addView(panelButtons[index],pp);

        Button showMaintenance=button("组件维护  ↻",view -> flipComponentCard(component));
        LinearLayout.LayoutParams maintenanceParams=new LinearLayout.LayoutParams(-1,dp(44)); maintenanceParams.topMargin=dp(8);
        front.addView(showMaintenance,maintenanceParams);
        LinearLayout back=(LinearLayout) component.getChildAt(1);
        TextView backKicker=ShellCards.text(this,index==0 ? "KIRAAI CORE · 维护卡" : "NAPCAT · 维护卡",10,index==0 ? ShellTheme.ACCENT : ShellTheme.TEAL);
        backKicker.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        back.addView(backKicker,new LinearLayout.LayoutParams(-1,-2));
        TextView backTitle=ShellCards.text(this,index==0 ? "核心服务" : "桥接服务",24,INK);
        backTitle.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        LinearLayout.LayoutParams backTitleParams=new LinearLayout.LayoutParams(-1,-2); backTitleParams.topMargin=dp(18); back.addView(backTitle,backTitleParams);
        TextView backCopy=ShellCards.text(this,index==0
            ? "管理 KiraAI 的运行状态、官方面板和访问令牌。"
            : "按需安装本机 NapCat，或在 KiraAI 官方面板配置远程连接。",13,MUTED);
        backCopy.setLineSpacing(dp(4),1f);
        LinearLayout.LayoutParams backCopyParams=new LinearLayout.LayoutParams(-1,-2); backCopyParams.topMargin=dp(10); back.addView(backCopy,backCopyParams);
        if (index==0) {
            addInlineAction(back,"查看完整状态",this::showDeviceStatus);
            addInlineAction(back,"KiraAI 首次默认令牌",() -> showInitialToken("kira"));
            addInlineAction(back,"KiraAI 备份管理",() -> backups.show());
        } else {
            addInlineAction(back,"使用远程 NapCat",this::showRemoteNapcat);
            addInlineAction(back,"NapCat 日志",() -> showLog("napcat"));
            addInlineAction(back,"更新 NapCat",this::confirmNapcatUpdate);
        }
        addInlineAction(back,"返回运行控制  ↻",() -> flipComponentCard(component));
    }

    /** NapCat 来源说明：白底细边框，和下方淡紫状态框区分开。 */
    private LinearLayout forkNotice() {
        LinearLayout box=ShellCards.column(this,0);
        box.setPadding(dp(13),dp(11),dp(13),dp(12));
        box.setBackground(ShellTheme.surface(this,ShellTheme.SURFACE,16,ShellTheme.LINE));
        TextView title=ShellCards.text(this,"这是 fork 版本，不是原版仓库",12,ShellTheme.TEAL);
        title.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        box.addView(title,new LinearLayout.LayoutParams(-1,-2));
        String[][] rows={
            {"原仓库","NapNeko/NapCatQQ 已删库，发布版本和官方安装脚本都无法再下载。"},
            {"NapCat","改用 xxynet/NapCatQQ 的 fork，首次安装固定 v4.9.43。"},
            {"安装脚本","改用 xiwangly2/NapCat-Installer 的 fork，固定到与原版相同的提交。"},
            {"校验","每个下载文件都按固定的大小和 SHA-256 校验，不一致就停止安装。"}};
        for (String[] row : rows) {
            LinearLayout line=new LinearLayout(this);
            TextView key=ShellCards.text(this,row[0],11,INK);
            key.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
            line.addView(key,new LinearLayout.LayoutParams(dp(58),-2));
            TextView value=ShellCards.text(this,row[1],11,MUTED); value.setLineSpacing(dp(2),1);
            line.addView(value,new LinearLayout.LayoutParams(0,-2,1));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2); lp.topMargin=dp(7);
            box.addView(line,lp);
        }
        return box;
    }

    private LinearLayout.LayoutParams forkNoticeParams() {
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2); params.topMargin=dp(12);
        return params;
    }

    /** 按当前字体缩放预留行高，避免中英文字体回退和长短文案改变卡片尺寸。 */
    private int stableTextHeight(TextView view,int lines) {
        return (int)Math.ceil(view.getTextSize()*1.65f)*lines+view.getPaddingTop()+view.getPaddingBottom();
    }

    private TextView label(LinearLayout parent,String text,int size) {
        TextView view=ShellCards.text(this,text,size,INK); view.setPadding(0,dp(5),0,dp(10));
        parent.addView(view,new LinearLayout.LayoutParams(-1,-2)); return view;
    }
    private ProgressBar bar(LinearLayout parent) {
        ProgressBar bar=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal); bar.setMax(100); ShellDialogs.styleProgress(bar);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,dp(6)); p.bottomMargin=dp(10); parent.addView(bar,p); return bar;
    }
    private Button iconButton(int kind,String description,View.OnClickListener action) {
        Button button=new Button(this) {
            @Override protected void onDraw(Canvas canvas) {
                super.onDraw(canvas);
                float size=Math.min(getWidth(),getHeight())*.65f;
                ShellIcons.draw(canvas,kind,(getWidth()-size)/2,(getHeight()-size)/2,size);
            }
        };
        button.setText("");button.setOnClickListener(action);button.setContentDescription(description);styleButton(button,false);return button;
    }

    private Button button(String title,View.OnClickListener action) {
        Button button=new Button(this); button.setText(title); button.setOnClickListener(action); styleButton(button,false); return button;
    }
    private GradientDrawable surface(int top,int bottom,int radius,int edge) {
        int color=Color.alpha(top)<255 ? ShellTheme.SOFT : top;
        return ShellTheme.surface(this,color,Math.min(radius,8),Color.alpha(edge)>0 ? ShellTheme.LINE : Color.TRANSPARENT);
    }
    private FrameLayout card(LinearLayout parent,int index) {
        FrameLayout card=flipFrame();
        card.setBackground(ShellTheme.glass(this,26).tint(index==0 ? ShellTheme.BLOOM_ROSE : ShellTheme.BLOOM_LILAC));
        card.setClipToOutline(true);
        ShellTheme.lift(card,8);
        LinearLayout front=new LinearLayout(this); front.setOrientation(LinearLayout.VERTICAL); front.setPadding(dp(19),dp(19),dp(19),dp(19));
        LinearLayout back=new LinearLayout(this); back.setOrientation(LinearLayout.VERTICAL); back.setPadding(dp(19),dp(19),dp(19),dp(19));
        back.setVisibility(View.INVISIBLE);
        // 卡片本身按圆角轮廓裁剪；两面不再按内边距裁剪，按钮光晕可以铺进留白。
        front.setClipToPadding(false); front.setClipChildren(false); back.setClipToPadding(false); back.setClipChildren(false);
        card.addView(front,new FrameLayout.LayoutParams(-1,-2)); card.addView(back,new FrameLayout.LayoutParams(-1,-2));
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2); p.bottomMargin=dp(14); parent.addView(card,p); componentCards.add(card); return card;
    }

    private FrameLayout flipFrame() {
        FrameLayout frame=new FrameLayout(this) {
            @Override public boolean onInterceptTouchEvent(android.view.MotionEvent event) {
                return Boolean.TRUE.equals(getTag()) || super.onInterceptTouchEvent(event);
            }
            @Override public boolean onTouchEvent(android.view.MotionEvent event) {
                return Boolean.TRUE.equals(getTag()) || super.onTouchEvent(event);
            }
        };
        frame.setCameraDistance(dp(1400));
        return frame;
    }

    /** 双面始终参与测量，只切换可见性；翻面不会改变卡片高度。 */
    private void flipComponentCard(FrameLayout card) {
        if (Boolean.TRUE.equals(card.getTag())) return;
        final View front=card.getChildAt(0), back=card.getChildAt(1);
        final boolean showingBack=back.getVisibility()==View.VISIBLE;
        Runnable swap=() -> {
            front.setVisibility(showingBack ? View.VISIBLE : View.INVISIBLE);
            back.setVisibility(showingBack ? View.INVISIBLE : View.VISIBLE);
            if(card==environmentFaces) environmentFlip.setText(showingBack ? "运行概览  ↻" : "返回环境  ↻");
        };
        if (!android.animation.ValueAnimator.areAnimatorsEnabled()) { swap.run(); return; }
        card.setTag(Boolean.TRUE);
        card.animate().cancel();
        // 翻到侧立时略微收小，再带一点回弹落回原位；只动变换属性，布局尺寸始终不变。
        card.animate().rotationY(showingBack ? 90f : -90f).scaleX(.94f).scaleY(.94f).setDuration(ShellTheme.FLIP/2).setInterpolator(ShellTheme.BOTH).withEndAction(() -> {
            swap.run(); card.setRotationY(showingBack ? -90f : 90f);
            card.animate().rotationY(0f).scaleX(1f).scaleY(1f).setDuration(ShellTheme.FLIP/2).setInterpolator(ShellTheme.ENTER)
                .withEndAction(() -> { card.setRotationY(0f); card.setScaleX(1f); card.setScaleY(1f); card.setTag(Boolean.FALSE); }).start();
        }).start();
    }

    private void styleButton(Button button,boolean primary) { ShellTheme.button(button,BLUE,primary); }
    private Button addToolbar(LinearLayout parent,String title,View.OnClickListener action) {
        Button button=button(title,action); button.setTextSize(12); button.setPadding(dp(4),0,dp(4),0);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(48),1); if (parent.getChildCount()>0) p.leftMargin=dp(8); parent.addView(button,p); return button;
    }

    @SuppressWarnings("deprecation") private void configureWebView(int index) {
        WebView panel = panelViews[index];
        PanelDownloads downloads = panelDownloads[index];
        WebSettings settings = panel.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(false);
        if (index == 0) panel.addJavascriptInterface(new PanelNight(), ShellWebTheme.BRIDGE);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(panel, false);
        panel.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String url) {
                if (local(Uri.parse(url))) {
                    panelLoading[index]=false;workspace.documentFinished(index+1);
                    downloads.installHook();
                    if (index == 0) ShellWebTheme.install(view);
                }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (local(uri)) return false;
                if (localBlob(uri.toString()) && sameOrigin(Uri.parse(uri.getSchemeSpecificPart()),
                        Uri.parse(view.getUrl() == null ? "" : view.getUrl()))) return false;
                if (request.isForMainFrame() && request.hasGesture() && ("https".equals(uri.getScheme()) || "http".equals(uri.getScheme()))) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
                    catch (Exception ignored) { toast("无法打开外部链接"); }
                }
                return true;
            }
            @Override public void onReceivedError(WebView view, android.webkit.WebResourceRequest request, android.webkit.WebResourceError error) {
                if (request.isForMainFrame() && local(request.getUrl())) {
                    panelsLoaded[index] = false;panelLoading[index]=false;workspace.documentFailed(index+1);
                    nextPanelAttempt[index] = android.os.SystemClock.elapsedRealtime() + 1500;
                    if ((index == 0 ? "kira" : "napcat").equals(selectedPanel)) {
                        showingEnvironment = true;
                        syncWorkspace(true);
                        detail.setText("组件正在启动或重启，面板就绪后会重试。");
                    }
                }
            }
        });
        panel.setDownloadListener((url, userAgent, disposition, mime, size) -> downloads.request(url, disposition, mime));
        panel.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view,int progress) {
                if(panelLoading[index]) workspace.documentProgress(index+1,progress);
            }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (!local(Uri.parse(view.getUrl() == null ? "" : view.getUrl()))) return false;
                if (upload != null) upload.onReceiveValue(null);
                upload = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                java.util.LinkedHashSet<String> accepted = new java.util.LinkedHashSet<>();
                for (String field : params.getAcceptTypes()) for (String part : field.split(",")) {
                    String type = part.trim().toLowerCase(java.util.Locale.ROOT);
                    if (type.startsWith(".")) type = android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(type.substring(1));
                    if (type != null && type.matches("[a-z0-9!#$&^_.+-]+/(?:[a-z0-9!#$&^_.+-]+|\\*)")) accepted.add(type);
                }
                if (accepted.size() == 1) intent.setType(accepted.iterator().next());
                else if (accepted.size() > 1) intent.putExtra(Intent.EXTRA_MIME_TYPES, accepted.toArray(new String[0]));
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try { startActivityForResult(intent, CHOOSE_UPLOAD); }
                catch (Exception error) { upload.onReceiveValue(null); upload = null; toast("无法打开文件选择器"); }
                return true;
            }
        });
    }

    static boolean local(Uri uri) {
        return "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost())
            && (uri.getPort() == 5267 || uri.getPort() == 6099) && uri.getUserInfo() == null;
    }

    static boolean localBlob(String url) {
        Uri uri = Uri.parse(url);
        return "blob".equals(uri.getScheme()) && local(Uri.parse(uri.getSchemeSpecificPart()));
    }

    private static boolean sameOrigin(Uri first, Uri second) {
        return local(first) && local(second) && first.getPort() == second.getPort();
    }

    private void render() {
        if(navigationQueued)return;
        JSONObject value = RuntimeStatus.read(this);
        boolean live = RuntimeStatus.alive(this, value);
        renderStatus(value,live);
    }

    private void renderStatus(JSONObject value,boolean live) {
        String state=value.optString("state","stopped");
        title.setText(RuntimeStatus.title(value));
        String message = value.isNull("detail") ? "点击启动，准备手机上的 Linux 与官方组件。" : value.optString("detail");
        detail.setText(message);
        int percent = RuntimeStatus.percent(value, "progress");
        boolean preparing = live && RuntimeStatus.preparing(value) && !"stopping".equals(state);
        overallText.setText(preparing ? (percent < 0 ? "正在准备环境" : "环境准备 · " + percent + "%")
            : live ? "共享环境已就绪" : "环境未运行");
        overallBar.setIndeterminate(preparing && percent < 0);
        overallBar.setProgress(preparing ? Math.max(0,percent) : live ? 100 : 0,true);
        RuntimeStatus.Transfer transfer = RuntimeStatus.Transfer.from(value);
        boolean transferring = live && transfer.active;
        transferBar.setVisibility(transferring ? View.VISIBLE : View.INVISIBLE);
        transferText.setText(preparing ? "等待下一项依赖任务…"
            : live ? "依赖准备已完成，可打开就绪的组件面板。" : "启动后显示系统依赖下载与安装进度。");
        transferDetail.setText("");
        if (transferring) {
            String[] lines=transfer.text.split("\n",2);
            transferText.setText(lines[0]);
            transferDetail.setText(lines.length>1 ? lines[1] : transfer.percent>=0 ? transfer.percent+"%" : "正在处理当前任务…");
            transferBar.setIndeterminate(transfer.percent < 0);
            if (transfer.percent >= 0) transferBar.setProgress(transfer.percent,true);
        }
        toggle.setText(live ? "停止全部" : "启动全部");
        toggle.setEnabled(!"stopping".equals(state));
        prepareStart.setEnabled(!live && !"stopping".equals(state));
        int readyCount = 0;
        int selectedCount = 0;
        boolean napcatInstalled = napcatInstalled(value);
        boolean selectedAvailable = false;
        for (int index = 0; index < 2; index++) {
            String name = index == 0 ? "kira" : "napcat";
            JSONObject component = RuntimeStatus.component(value, name);
            String phase = component.optString("state", "stopped");
            boolean ready = live && "ready".equals(phase);
            boolean selected = selectedComponents.contains(name);
            if (selected) selectedCount++;
            if (!ready) panelsLoaded[index] = false;
            if (ready) readyCount++;
            String health = ready ? RuntimeStatus.panelHealth(this, component) : "unknown";
            panelHealth[index] = health;
            boolean available = RuntimeStatus.panelAvailable(this, component, live);
            if (name.equals(selectedPanel)) selectedAvailable = available;
            boolean installing = index == 1 && live && component.optBoolean("installing");
            boolean installFailed = index == 1 && component.optBoolean("install_failed");
            String lifecycle = installing ? "安装中" : installFailed ? "安装失败" : index == 1 && !napcatInstalled
                && "stopped".equals(phase) ? "未安装" : RuntimeStatus.phase(phase);
            String panelLabel = RuntimeStatus.panelHealthLabel(health);
            componentBadge[index].setText(ready ? panelLabel : lifecycle);
            componentBadge[index].setContentDescription(ready ? lifecycle + "，" + panelLabel : null);
            environmentComponents[index].setText(ready && !available ? panelLabel : lifecycle);
            boolean failed = "error".equals(phase);
            boolean warning = failed || (ready && !available);
            boolean observed = ready && "reachable".equals(health);
            componentBadge[index].setTextColor(observed ? BLUE : warning ? ShellTheme.ACCENT : MUTED);
            componentBadge[index].setBackground(surface(observed ? ShellTheme.ROSE_SOFT : warning ? 0xFFF7E5EC : ShellTheme.SOFT,
                observed ? ShellTheme.ROSE_SOFT : warning ? 0xFFF3DCE7 : ShellTheme.SOFT, 12, Color.WHITE));
            String text = index == 0 ? "AI 会话与插件" : napcatInstalled ? "本机 NapCat" : "本机可选 · 也支持远程";
            text += selected ? " · 已选择" : " · 未选择";
            String version = component.isNull("version") ? "" : component.optString("version");
            if (!version.isEmpty()) text = version + "  ·  " + text;
            String componentDetail = component.isNull("detail") ? "" : component.optString("detail");
            if (!componentDetail.isEmpty() && !(ready && "官方面板已就绪".equals(componentDetail))) text += "\n" + componentDetail;
            String error = component.isNull("error") ? "" : component.optString("error");
            if (!error.isEmpty() && !error.equals(componentDetail)) text += "\n" + error;
            String backup = component.isNull("backup") ? "" : component.optString("backup");
            if (!backup.isEmpty()) text += "\n最近备份：" + new File(backup).getName();
            componentStatus[index].setText(text);
            boolean active = live && RuntimeStatus.active(component);
            boolean changing = active && !"ready".equals(phase) && !"running".equals(phase);
            componentStart[index].setText(index == 1 && !napcatInstalled ? "安装本机 NapCat" : "启动");
            componentStart[index].setEnabled(!active && !"stopping".equals(state));
            componentStop[index].setText(active ? "停止" : "取消选择");
            componentStop[index].setEnabled((active || selected) && !"stopping".equals(phase) && !"stopping".equals(state));
            componentRestart[index].setEnabled(active && !changing && !"stopping".equals(state));
            setPanelButtonStatus(panelButtons[index], ready, available, panelLabel);
            setPanelButtonStatus(preparationPanels[index], ready, available, panelLabel);
            navigation.setPanelStatus(index + 1, ready, available, health);
        }
        workspaceState.setText("已选择 " + selectedCount + " · 就绪 " + readyCount);
        String indicator=RuntimeStatus.shellState(value,live);
        localIndicator.setText("● " + indicator);
        int indicatorColor="运行中".equals(indicator) ? BLUE : preparing ? ShellTheme.TEAL : ShellTheme.MUTED;
        localIndicator.setTextColor(indicatorColor);
        localIndicator.setBackground(ShellTheme.surface(this,ShellTheme.tint(indicatorColor),18,0xFFFFFFFF));
        drawer.setRuntimeStatus(value,live);
        int selectedIndex = "napcat".equals(selectedPanel) ? 1 : 0;
        if (selectedAvailable && !panelsLoaded[selectedIndex] && android.os.SystemClock.elapsedRealtime() >= nextPanelAttempt[selectedIndex]) {
            panelsLoaded[selectedIndex] = true;panelLoading[selectedIndex]=true;
            workspace.beginNavigation(selectedIndex+1,true);
            panelViews[selectedIndex].loadUrl(selectedIndex == 1 ? NAPCAT_PANEL : PANEL);
            showingEnvironment = false;
        }
        syncWorkspace(true);
    }

    private void setPanelButtonStatus(Button button, boolean ready, boolean available, String panelLabel) {
        button.setEnabled(available);
        CharSequence description = ready ? button.getText() + "，" + panelLabel : null;
        if (!android.text.TextUtils.equals(button.getContentDescription(), description)) button.setContentDescription(description);
    }

    private void syncWorkspace(boolean animate) {
        int index = showingEnvironment ? 0 : "napcat".equals(selectedPanel) ? 2 : 1;
        web = panelViews[index == 2 ? 1 : 0];
        setCompactChrome(index != 0);
        workspace.showPage(index, animate);
        navigation.select(index, animate);
        updateNight(animate);
    }

    /** 只在显示 KiraAI 面板且面板是夜间时变深；首页和 NapCat 面板保持日间。 */
    private void updateNight(boolean animate) {
        float target = panelDark && !showingEnvironment && !"napcat".equals(selectedPanel) ? 1f : 0f;
        // 状态刷新会反复调用这里；已在朝同一方向渐变时不打断。
        if (nightAnimator != null && nightTarget == target) return;
        nightTarget = target;
        if (nightAnimator != null) { nightAnimator.removeAllUpdateListeners(); nightAnimator.cancel(); nightAnimator = null; }
        if (target == night) return;
        if (!animate || !ValueAnimator.areAnimatorsEnabled() || !screenFrame.isAttachedToWindow()) { applyNight(target); return; }
        // 与官方面板的主题渐变同样 250 毫秒，四周和面板一起变色。
        ValueAnimator next = ValueAnimator.ofFloat(night, target);
        next.setDuration(250); next.setInterpolator(ShellTheme.BOTH);
        next.addUpdateListener(value -> applyNight((float) value.getAnimatedValue()));
        next.addListener(new android.animation.AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(android.animation.Animator animation) { if (nightAnimator == animation) nightAnimator = null; }
        });
        nightAnimator = next; next.start();
    }

    @SuppressWarnings("deprecation") private void applyNight(float value) {
        boolean wasDark = night >= .5f;
        night = value;
        nightVeil.setAlpha(value); nightVeil.setVisibility(value > 0f ? View.VISIBLE : View.INVISIBLE);
        int status = ShellTheme.blend(ShellTheme.DAY_STATUS, ShellTheme.NIGHT_PAGE, value);
        screenFrame.setBackgroundColor(status);
        getWindow().setStatusBarColor(status);
        getWindow().setNavigationBarColor(ShellTheme.blend(ShellTheme.DAY_NAVIGATION, ShellTheme.NIGHT_PAGE, value));
        menuNight.setAlpha(Math.round(255 * value));
        brand.setTextColor(ShellTheme.blend(INK, ShellTheme.NIGHT_INK, value));
        caption.setTextColor(ShellTheme.blend(MUTED, ShellTheme.NIGHT_MUTED, value));
        navigation.setNight(value);
        // 系统栏图标在渐变过半时换色，避免浅底浅字或深底深字。
        if ((value >= .5f) != wasDark) lightSystemBars(value < .5f);
    }

    @SuppressWarnings("deprecation") private void lightSystemBars(boolean light) {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController controller = getWindow().getInsetsController();
            int both = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            if (controller != null) controller.setSystemBarsAppearance(light ? both : 0, both);
        } else {
            View decor = getWindow().getDecorView();
            int flags = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            int current = decor.getSystemUiVisibility();
            decor.setSystemUiVisibility(light ? current | flags : current & ~flags);
        }
    }

    /** KiraAI 面板 WebView 唯一的原生接口：报告面板是否夜间。只接收一个布尔值，不读写任何数据。 */
    private final class PanelNight {
        @JavascriptInterface public void dark(boolean value) {
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed() || panelDark == value) return;
                panelDark = value;
                // 面板重新加载、尚未绘制时露出的底色也跟着面板主题，不会在夜间闪一下浅色。
                panelViews[0].setBackgroundColor(value ? ShellTheme.NIGHT_PAGE : ShellTheme.PAGE);
                updateNight(true);
            });
        }
    }

    private void setCompactChrome(boolean compact) {
        brand.setText(compact && "napcat".equals(selectedPanel) ? "NapCat" : "KiraAI");
        String health = panelHealth["napcat".equals(selectedPanel) ? 1 : 0];
        boolean blocked = compact && ("unreachable".equals(health) || "expired".equals(health));
        String captionText = blocked ? RuntimeStatus.panelHealthLabel(health) + " · 暂停刷新" : "本机组件管理";
        if (!captionText.contentEquals(caption.getText())) caption.setText(captionText);
        int captionVisibility = !compact || blocked ? View.VISIBLE : View.GONE;
        if (caption.getVisibility() != captionVisibility) caption.setVisibility(captionVisibility);
        if (compactChrome == compact) return;
        compactChrome = compact;
        localIndicator.setVisibility(compact ? View.GONE : View.VISIBLE);
        header.setPadding(dp(compact ? 12 : 20), dp(compact ? 3 : 8), dp(compact ? 12 : 20), dp(compact ? 3 : 4));
        LinearLayout.LayoutParams menuParams = (LinearLayout.LayoutParams) menuButton.getLayoutParams();
        menuParams.width = dp(44); menuParams.height = dp(44); menuButton.setLayoutParams(menuParams);
        menuButton.setTextSize(compact ? 20 : 22);
        brand.setTextSize(compact ? 16 : 16);
        brand.setPadding(0, 0, 0, 0);
        dock.setPadding(dp(compact ? 10 : 22), dp(compact ? 3 : 8), dp(compact ? 10 : 22), dp(compact ? 3 : 13));
        LinearLayout.LayoutParams navParams = (LinearLayout.LayoutParams) navigation.getLayoutParams();
        navParams.height = dp(compact ? 52 : 64); navigation.setLayoutParams(navParams);
        navigation.setCompact(compact);
    }

    private void startRuntime() {
        if (!canStartSelected(RuntimeStatus.read(this))) {
            toast("请先选择要启动的组件");
            return;
        }
        resumeAutomaticStart();
        sendComponentCommand("all", "start");
    }

    private void toggleRuntime() {
        if (RuntimeStatus.alive(this)) {
            RuntimeStatus.setManuallyStopped(this, true);
            preferences.edit().putBoolean("manually_stopped", true).apply();
            try { startService(new Intent(this, HostService.class).setAction(HostService.STOP)); }
            catch (RuntimeException error) { toast("无法提交停止操作，请重试"); }
            showingEnvironment = true;
        } else { panelsLoaded[0] = false; panelsLoaded[1] = false; startRuntime(); }
    }

    private boolean napcatInstalled(JSONObject value) {
        return RuntimeStatus.component(value, "napcat").optBoolean("installed", legacyNapcatInstalled);
    }

    private boolean canStartSelected(JSONObject value) {
        return selectedComponents.contains("kira") || (selectedComponents.contains("napcat") && napcatInstalled(value));
    }

    private void startComponent(String component) {
        if ("napcat".equals(component) && !napcatInstalled(RuntimeStatus.read(this))) confirmNapcatInstall();
        else invoke(component, "start");
    }

    private void resumeAutomaticStart() {
        RuntimeStatus.setManuallyStopped(this, false);
        preferences.edit().putBoolean("manually_stopped", false).apply();
    }

    private void invoke(String component, String action) {
        boolean stop = "stop".equals(action);
        boolean start = "start".equals(action) || "restart".equals(action) || "install".equals(action);
        if ("napcat".equals(component) && ("update".equals(action) || "start".equals(action) || "restart".equals(action))
                && !napcatInstalled(RuntimeStatus.read(this))) {
            toast("请先安装本机 NapCat");
            return;
        }
        if (start || stop) {
            try { selectedComponents = ComponentSelection.persist(this, component, start); }
            catch (IOException failure) {
                toast("无法保存组件选择，请检查存储后重试");
                return;
            }
            int index = "napcat".equals(component) ? 1 : 0;
            panelsLoaded[index] = false;
            nextPanelAttempt[index] = 0;
            if (start) resumeAutomaticStart();
        }
        sendComponentCommand(component, action);
        showEnvironment();
    }

    private void sendComponentCommand(String component, String action) {
        sendComponentCommand(component, action, false);
    }

    private void sendComponentCommand(String component, String action, boolean resume) {
        Intent command = new Intent(this, HostService.class).setAction(HostService.CONTROL)
            .putExtra(HostService.COMPONENT, component).putExtra(HostService.ACTION, action)
            .putExtra(HostService.RESUME, resume);
        try {
            if ("stop".equals(action)) startService(command);
            else startForegroundService(command);
        }
        catch (RuntimeException error) { toast("无法提交组件操作，请重新打开应用后重试"); }
    }

    private void showPanel(String component) {
        queuePanelAction(component, false);
    }

    private void showEnvironment() {
        selectedPanel=null;showingEnvironment=true;
        queueNavigation(0,false,this::render);
    }

    private void queueNavigation(int target,boolean loading,Runnable action) {
        int generation=++navigationGeneration;navigationQueued=true;
        workspace.beginNavigation(target,loading);
        // 先给进度条一个绘制帧，再执行 WebView 加载，避免点击后几秒没有可见反馈。
        workspace.postOnAnimation(()->workspace.post(()->{
            if(generation!=navigationGeneration||isFinishing()||isDestroyed())return;
            navigationQueued=false;action.run();
        }));
    }

    private void refreshPanel(String component) {
        queuePanelAction(component, true);
    }

    private boolean allowPanelAction(JSONObject value, boolean live, String name) {
        JSONObject component = RuntimeStatus.component(value, name);
        if (RuntimeStatus.panelAvailable(this, component, live)) return true;
        String label = "napcat".equals(name) ? "NapCat" : "KiraAI";
        if (live && "ready".equals(component.optString("state"))) {
            toast(label + " · " + RuntimeStatus.panelHealthLabel(RuntimeStatus.panelHealth(this, component)));
        } else {
            toast("请等待 " + label + " 面板就绪");
        }
        return false;
    }

    private void queuePanelAction(String component, boolean reload) {
        JSONObject currentStatus = RuntimeStatus.read(this);
        if (!allowPanelAction(currentStatus, RuntimeStatus.alive(this, currentStatus), component)) return;
        int index = "napcat".equals(component) ? 1 : 0;
        queueNavigation(index + 1, reload || !panelsLoaded[index] || panelLoading[index], () -> {
            JSONObject value = RuntimeStatus.read(this);
            boolean live = RuntimeStatus.alive(this, value);
            // Health may change while the navigation frame is queued.
            if (!allowPanelAction(value, live, component)) {
                workspace.cancelLoading();
                renderStatus(value, live);
                return;
            }
            selectedPanel = component;
            showingEnvironment = false;
            if (reload) {
                panelLoading[index] = true;
                panelsLoaded[index] = true;
                String address = index == 0 ? PANEL : NAPCAT_PANEL;
                String current = panelViews[index].getUrl();
                if (sameOrigin(Uri.parse(current == null ? "" : current), Uri.parse(address))) panelViews[index].reload();
                else panelViews[index].loadUrl(address);
            }
            renderStatus(value, live);
        });
    }

    private void menuAction(int which) {
        if (which == 0 || which == 1) showInitialToken(which == 0 ? "kira" : "napcat");
        else if (which == 2) refreshPanel("kira");
        else if (which == 12) refreshPanel("napcat");
        else if (which == 3) backups.show();
        else if (which == 4) confirmNapcatUpdate();
        else if (which == 13) confirmNapcatUninstall();
        else if (which == 14) onboarding.show();
        else if (which == 5) updater.show();
        else if (which == 9) batterySettings();
        else if (which == 10) showAbout();
        else if (which == 11) editEnvironment();
        else if (which == 6 || which == 8) showLog(which == 8 ? "napcat" : "environment");
    }

    private TextView dialogCopy(LinearLayout parent, String text, int size, int color, int top, int bottom) {
        TextView copy = ShellCards.text(this, text, size, color);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(top);
        params.bottomMargin = dp(bottom);
        parent.addView(copy, params);
        return copy;
    }

    /** Archive cards use a vertical process rail instead of a row of generic tiles. */
    private void addArchiveStep(LinearLayout parent, String number, String title, String body, boolean last) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.TOP);
        FrameLayout rail = new FrameLayout(this);
        TextView marker = ShellCards.text(this, number, 12, BLUE);
        marker.setGravity(Gravity.CENTER);
        marker.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        marker.setBackground(ShellTheme.surface(this,ShellTheme.ROSE_SOFT,18,Color.TRANSPARENT));
        rail.addView(marker, new FrameLayout.LayoutParams(dp(40), dp(40), Gravity.TOP | Gravity.CENTER_HORIZONTAL));
        if (!last) {
            View spine = new View(this);
            spine.setBackgroundColor(ShellTheme.LINE);
            FrameLayout.LayoutParams spineParams = new FrameLayout.LayoutParams(dp(2), dp(58), Gravity.TOP | Gravity.CENTER_HORIZONTAL);
            spineParams.topMargin = dp(40);
            rail.addView(spine, spineParams);
        }
        row.addView(rail, new LinearLayout.LayoutParams(dp(52), dp(last ? 58 : 92)));
        LinearLayout copy = ShellCards.column(this, 0);
        copy.setPadding(dp(8), dp(1), 0, dp(14));
        TextView heading = dialogCopy(copy, title, 15, INK, 0, 4);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        dialogCopy(copy, body, 12, MUTED, 0, 0);
        row.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));
        parent.addView(row, new LinearLayout.LayoutParams(-1, -2));
    }

    private void addVersionTile(LinearLayout parent, String name, String version, int accent) {
        LinearLayout tile = ShellCards.column(this, 12);
        tile.setPadding(dp(14), dp(12), dp(14), dp(13));
        tile.setBackground(ShellTheme.gradient(this,ShellTheme.SURFACE,ShellTheme.PEARL_END,18,ShellTheme.LINE));
        LinearLayout ticketHead = new LinearLayout(this);
        ticketHead.setGravity(Gravity.CENTER_VERTICAL);
        TextView eyebrow = ShellCards.text(this, "已安装组件", 10, ShellTheme.MUTED);
        eyebrow.setTypeface(Typeface.MONOSPACE);
        ticketHead.addView(eyebrow, new LinearLayout.LayoutParams(0, -2, 1));
        TextView dot = ShellCards.text(this, "●", 10, accent);
        ticketHead.addView(dot);
        tile.addView(ticketHead, new LinearLayout.LayoutParams(-1, -2));
        TextView component = dialogCopy(tile, name, 17, accent, 9, 2);
        component.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        TextView value = dialogCopy(tile, version, 12, INK, 0, 6);
        value.setTypeface(Typeface.MONOSPACE);
        value.setTextIsSelectable(true);
        View cut = new View(this);
        cut.setBackgroundColor(ShellTheme.LINE);
        tile.addView(cut, new LinearLayout.LayoutParams(-1, dp(1)));
        TextView footer = dialogCopy(tile, "本地版本", 10, MUTED, 6, 0);
        footer.setTypeface(Typeface.MONOSPACE);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
        if (parent.getChildCount() > 0) params.leftMargin = dp(10);
        parent.addView(tile, params);
    }

    private void addBatteryTip(LinearLayout parent, String title, String note) {
        LinearLayout tip = new LinearLayout(this);
        tip.setGravity(Gravity.TOP);
        tip.setPadding(dp(2), dp(15), dp(2), 0);
        TextView dot = ShellCards.text(this, "•", 17, ShellTheme.TEAL);
        dot.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        tip.addView(dot, new LinearLayout.LayoutParams(dp(18), -2));
        LinearLayout copy = ShellCards.column(this, 0);
        copy.setPadding(0, 0, 0, 0);
        TextView heading = dialogCopy(copy, title, 13, INK, 0, 4);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        dialogCopy(copy, note, 12, MUTED, 0, 0);
        tip.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));
        parent.addView(tip, new LinearLayout.LayoutParams(-1, -2));
    }

    private void addLogToolbar(LinearLayout parent, boolean terminal) {
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(10), dp(8), dp(10), dp(8));
        TextView mode = ShellCards.text(this, terminal ? "LIVE TAIL" : "INSTALL TAIL", 10,
            terminal ? 0xFFE6C9F0 : ShellTheme.MUTED);
        mode.setTypeface(Typeface.MONOSPACE);
        toolbar.addView(mode, new LinearLayout.LayoutParams(0, -2, 1));
        TextView range = ShellCards.text(this, terminal ? "stdout  ·  utf-8" : "last 32 KiB  ·  utf-8", 10,
            terminal ? 0xFFD3BEDA : ShellTheme.MUTED);
        range.setTypeface(Typeface.MONOSPACE);
        toolbar.addView(range);
        parent.addView(toolbar, new LinearLayout.LayoutParams(-1, -2));
    }

    private void tintLogText(View view, int color) {
        if (view instanceof TextView) ((TextView) view).setTextColor(color);
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int index = 0; index < group.getChildCount(); index++) tintLogText(group.getChildAt(index), color);
        }
    }

    private void confirmKiraBackup() {
        LinearLayout content = ShellCards.column(this, 20);
        ShellCards.section(content, "", "先保存，再恢复运行", "备份程序、配置、插件与聊天，期间暂时停止 KiraAI，完成后恢复此前运行状态。", BLUE);
        LinearLayout process = ShellCards.column(this, 0);
        process.setPadding(dp(4), dp(3), 0, dp(3));
        addArchiveStep(process, "01", "暂停 KiraAI", "备份期间暂时停止组件，避免运行中的文件发生变化。", false);
        addArchiveStep(process, "02", "验证并保留", "新备份验证成功后，自动清理最旧的完整备份，最多保留2份。", false);
        addArchiveStep(process, "03", "继续使用官方面板", "KiraAI 程序更新请使用官方面板。", true);
        content.addView(process, new LinearLayout.LayoutParams(-1, -2));
        ShellDialogs.builder(this, ShellDialogs.Card.ARCHIVE).setTitle("新建 KiraAI 备份").setView(ShellDialogs.scroll(this, content, .42f))
            .setPositiveButton("开始备份", (dialog, which) -> invoke("kira", "backup"))
            .setNegativeButton("取消", null).show();
    }

    private void confirmNapcatUpdate() {
        if (!napcatInstalled(RuntimeStatus.read(this))) {
            toast("本机 NapCat 尚未安装，请先在运行组件中安装");
            return;
        }
        LinearLayout content = ShellCards.column(this, 20);
        LinearLayout release = new LinearLayout(this); release.setGravity(Gravity.CENTER_VERTICAL);
        release.setPadding(dp(16),dp(14),dp(16),dp(14));
        release.setBackground(ShellTheme.surface(this,ShellTheme.ROSE_SOFT,18,Color.TRANSPARENT));
        release.addView(ShellIcons.view(this,ShellIcons.UPDATE),new LinearLayout.LayoutParams(dp(48),dp(48)));
        LinearLayout copy=new LinearLayout(this); copy.setOrientation(LinearLayout.VERTICAL); copy.setPadding(dp(12),0,0,0);
        copy.addView(ShellCards.text(this,"NapCat 更新",18,INK));
        copy.addView(ShellCards.text(this,"先备份，再更新",12,ShellTheme.MUTED));
        release.addView(copy,new LinearLayout.LayoutParams(0,-2,1)); content.addView(release);
        ShellCards.step(content,"01","暂停并备份","备份程序与 QQ 数据，保留当前状态。",ShellTheme.TEAL);
        ShellCards.step(content,"02","执行更新","从 xxynet/NapCatQQ 的 fork 获取新版本（原 NapNeko 仓库已删库），只更新 NapCat，不改变 KiraAI 配置。",ShellTheme.TEAL);
        ShellCards.step(content,"03","恢复运行","按更新前状态恢复组件；结果可在日志查看。",ShellTheme.TEAL);
        ShellDialogs.builder(this, ShellDialogs.Card.NAPCAT_UPDATE).setTitle("更新 NapCat").setView(ShellDialogs.scroll(this, content, .42f))
            .setPositiveButton("开始更新", (confirm, button) -> invoke("napcat", "update"))
            .setNegativeButton("取消", null).show();
    }

    /** R5C4: explicit uninstall of the local NapCat program. Settings are kept; QQ login data only goes on a second confirmation. */
    private void confirmNapcatUninstall() {
        JSONObject paths = RuntimeStatus.read(this).optJSONObject("paths");
        String programPath = paths == null ? "" : paths.optString("napcat_program", "");
        String dataPath = paths == null ? "" : paths.optString("napcat_data", "");
        File program = new File(getFilesDir(), programPath);
        if (programPath.isEmpty() || !program.exists()) {
            toast("本机没有安装 NapCat");
            return;
        }
        File data = dataPath.isEmpty() ? null : new File(getFilesDir(), dataPath);
        new Thread(() -> {
            long programBytes = treeBytes(program.toPath());
            long dataBytes = data == null ? 0 : treeBytes(data.toPath());
            runOnUiThread(() -> { if (!isFinishing() && !isDestroyed()) showNapcatUninstall(programBytes, dataBytes); });
        }, "napcat-uninstall-size").start();
    }

    private static long treeBytes(Path root) {
        long[] total = {0};
        if (!Files.isDirectory(root, java.nio.file.LinkOption.NOFOLLOW_LINKS)) return 0;
        try {
            Files.walkFileTree(root, new SimpleFileVisitor<Path>() {
                @Override public FileVisitResult visitFile(Path file, BasicFileAttributes attributes) {
                    if (attributes.isRegularFile()) total[0] += attributes.size();
                    return FileVisitResult.CONTINUE;
                }
                @Override public FileVisitResult visitFileFailed(Path file, IOException failure) { return FileVisitResult.CONTINUE; }
            });
        } catch (IOException ignored) { }
        return total[0];
    }

    private void showNapcatUninstall(long programBytes, long dataBytes) {
        LinearLayout content = ShellCards.column(this, 20);
        ShellCards.step(content,"01","保留配置与插件","NapCat 的配置、插件和面板令牌先复制到应用目录并逐个核对，以后重新安装本机 NapCat 时自动放回。",ShellTheme.TEAL);
        ShellCards.step(content,"02","删除程序","删除 NapCat 与 Linux QQ 程序，约 " + RuntimeStatus.bytes(programBytes) + "。KiraAI、共享的系统依赖和已有的 NapCat 更新备份不受影响。",ShellTheme.TEAL);
        ShellCards.step(content,"03","QQ 登录数据","默认保留（约 " + RuntimeStatus.bytes(dataBytes) + "），重新安装后可免重新登录。",ShellTheme.TEAL);
        CheckBox deleteData = new CheckBox(this);
        deleteData.setText("同时删除 QQ 登录数据");
        deleteData.setTextColor(INK);
        deleteData.setTextSize(13);
        deleteData.setMinHeight(dp(48));
        LinearLayout.LayoutParams checkParams = new LinearLayout.LayoutParams(-1, -2); checkParams.topMargin = dp(6);
        content.addView(deleteData, checkParams);
        ShellDialogs.builder(this, ShellDialogs.Card.NAPCAT_UPDATE).setTitle("卸载本机 NapCat").setView(ShellDialogs.scroll(this, content, .44f))
            .setPositiveButton("卸载", (dialog, button) -> {
                if (!deleteData.isChecked()) { submitNapcatUninstall(false); return; }
                ShellDialogs.builder(this, ShellDialogs.Card.NAPCAT_UPDATE).setTitle("删除 QQ 登录数据？")
                    .setMessage("QQ 的登录信息、聊天缓存和设置将被永久删除，无法恢复。重新安装后需要重新登录。")
                    .setPositiveButton("删除并卸载", (again, which) -> submitNapcatUninstall(true))
                    .setNegativeButton("取消", null).show();
            })
            .setNeutralButton("使用远程", (dialog, button) -> showRemoteNapcat())
            .setNegativeButton("取消", null).show();
    }

    /** One private request file authorises one uninstall; the host runtime consumes it. */
    private void submitNapcatUninstall(boolean deleteQqData) {
        File directory = RuntimeStatus.directory(this);
        File request = new File(directory, "napcat-uninstall-request.json");
        File temporary = new File(directory, "napcat-uninstall-request.json.tmp");
        try {
            if (Files.isSymbolicLink(request.toPath()) || Files.isSymbolicLink(temporary.toPath())) throw new IOException("Invalid request location");
            Files.createDirectories(directory.toPath());
            String body = new JSONObject().put("schema", "napcat-uninstall-request/v1").put("delete_qq_data", deleteQqData).toString();
            Files.write(temporary.toPath(), body.getBytes(StandardCharsets.UTF_8));
            Files.move(temporary.toPath(), request.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            selectedComponents = ComponentSelection.persist(this, "napcat", false);
        } catch (Exception failure) {
            toast("无法提交卸载，请重试");
            return;
        }
        invoke("napcat", "uninstall");
    }

    private void confirmNapcatInstall() {
        ShellDialogs.builder(this, ShellDialogs.Card.NAPCAT_UPDATE).setTitle("安装本机 NapCat")
            .setMessage("将在手机的 Linux 环境安装 NapCat、Linux QQ 和所需系统依赖。已有 KiraAI 和 QQ 数据会保留。\n\n"
                + "NapCat 来自 xxynet/NapCatQQ 的 fork 版本（首次安装 v4.9.43）：原 NapNeko/NapCatQQ 已删库，发布版本和官方安装脚本都无法再下载。"
                + "安装包和安装脚本仍逐一校验大小与 SHA-256。")
            .setPositiveButton("确认安装", (dialog, button) -> invoke("napcat", "install"))
            .setNeutralButton("使用远程", (dialog, button) -> showRemoteNapcat())
            .setNegativeButton("取消", null).show();
    }

    private void showRemoteNapcat() {
        ShellDialogs.builder(this, ShellDialogs.Card.REMOTE).setTitle("使用远程 NapCat")
            .setMessage("在 KiraAI 官方面板打开 QQ 适配器设置，填写服务器上的 NapCat OneBot 地址和令牌。如果手机已运行本机 NapCat，可以先停止它，安装数据会保留。")
            .setPositiveButton("打开 KiraAI 面板", (dialog, button) -> showPanel("kira"))
            .setNegativeButton("关闭", null).show();
    }

    private void editEnvironment() {
        String saved = RuntimeEnvironment.DEFAULT_TEXT;
        boolean unreadable = false;
        try { saved = RuntimeEnvironment.load(this); }
        catch (Exception failure) { unreadable = true; }
        LinearLayout content = ShellCards.column(this, 20);
        LinearLayout fileBar = new LinearLayout(this);
        fileBar.setGravity(Gravity.CENTER_VERTICAL);
        fileBar.setPadding(dp(13), dp(12), dp(13), dp(12));
        fileBar.setBackground(ShellTheme.surface(this,ShellTheme.SOFT,16,Color.TRANSPARENT));
        TextView fileName = ShellCards.text(this, "environment.json", 13, ShellTheme.MUTED);
        fileName.setTypeface(Typeface.MONOSPACE);
        fileBar.addView(fileName, new LinearLayout.LayoutParams(0, -2, 1));
        TextView syntax = ShellCards.text(this, "NAME=VALUE", 10, MUTED);
        syntax.setTypeface(Typeface.MONOSPACE);
        fileBar.addView(syntax);
        content.addView(fileBar, new LinearLayout.LayoutParams(-1, -2));
        dialogCopy(content, "每行 NAME=VALUE，无需添加引号。值中的空格与等号按原样保留，# 开头的整行作为备注。", 12, MUTED, 11, 7);
        dialogCopy(content, "保存后用于两个组件新启动的进程，正在运行的组件需重启。", 12, ShellTheme.MUTED, 0, 10);
        if (unreadable) {
            dialogCopy(content, "已保存的配置无法读取。可填写并保存新配置；取消会保留原文件。", 12, ShellTheme.ACCENT, 0, 10);
        }
        EditText editor = new EditText(this);
        editor.setText(saved);
        editor.setTextSize(13);
        editor.setTextColor(INK);
        editor.setTypeface(Typeface.MONOSPACE);
        editor.setGravity(Gravity.TOP | Gravity.LEFT);
        editor.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE
            | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        editor.setHorizontallyScrolling(false);
        editor.setMinLines(5);
        editor.setMaxLines(9);
        editor.setSelectAllOnFocus(false);
        editor.setContentDescription("环境变量，每行 NAME=VALUE");
        content.addView(ShellFields.wrap(this, "自定义变量", editor), new LinearLayout.LayoutParams(-1, -2));
        dialogCopy(content, "编辑器内容会完整保留，保存时只校验配置格式。", 11, MUTED, 7, 3);
        dialogCopy(content, "默认 KIRA_TELEMETRY_ENABLED=0，关闭 KiraAI 遥测。", 11, ShellTheme.MUTED, 0, 6);
        ScrollView scroll = ShellDialogs.scroll(this, content, .78f);
        AlertDialog dialog = ShellDialogs.builder(this, ShellDialogs.Card.EDITOR).setTitle("环境变量").setView(scroll)
            .setPositiveButton("保存", null).setNegativeButton("取消", null)
            .setNeutralButton("填入默认", null).create();
        dialog.setOnShowListener(ignored -> {
            if (dialog.getWindow() != null) {
                dialog.getWindow().setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
            }
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(view -> editor.setText(RuntimeEnvironment.DEFAULT_TEXT));
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
                try {
                    RuntimeEnvironment.save(this, editor.getText().toString());
                    dialog.dismiss();
                    toast("已保存，对新启动的进程生效");
                } catch (IllegalArgumentException invalid) { ShellFields.error(editor, invalid.getMessage()); }
                catch (Exception failure) { ShellFields.error(editor, "保存失败，原有设置保持不变。"); }
            });
        });
        dialog.show();
    }

    private void showAbout() {
        LinearLayout content = ShellCards.column(this, 24);
        LinearLayout badge = new LinearLayout(this);
        badge.setGravity(Gravity.CENTER_VERTICAL);
        badge.setPadding(dp(16), dp(12), dp(16), dp(12));
        badge.setBackgroundColor(Color.TRANSPARENT);
        badge.setPadding(0,dp(4),0,dp(16));
        // 关于页的标志就是 KiraAI 官方图标，和底栏、组件卡用同一张原图、同一圆角。
        View mark = ShellIcons.view(this, ShellIcons.CORE);
        mark.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        mark.setContentDescription("KiraAI");
        badge.addView(mark, new LinearLayout.LayoutParams(dp(52), dp(52)));
        LinearLayout brandCopy = ShellCards.column(this, 0);
        brandCopy.setPadding(dp(14), 0, 0, 0);
        TextView brand = ShellCards.text(this, "KiraAI", 24, INK);
        brand.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        brandCopy.addView(brand);
        TextView strap = ShellCards.text(this, "本地运行 · 随时连接", 11, ShellTheme.MUTED);
        strap.setTypeface(Typeface.MONOSPACE);
        LinearLayout.LayoutParams strapParams = new LinearLayout.LayoutParams(-1, -2);
        strapParams.topMargin = dp(3);
        brandCopy.addView(strap, strapParams);
        badge.addView(brandCopy, new LinearLayout.LayoutParams(0, -2, 1));
        content.addView(badge, new LinearLayout.LayoutParams(-1, -2));
        TextView intro = dialogCopy(content, "在手机本地管理 KiraAI 与 NapCat。\n组件启停、环境状态与官方面板，集中在一个应用中。", 13, MUTED, 8, 13);
        intro.setGravity(Gravity.LEFT);
        aboutVersionTaps=0;lastAboutVersionTap=0;
        TextView build = dialogCopy(content,"版本 "+BuildConfig.VERSION_NAME+"  ·  Build "+BuildConfig.VERSION_CODE,12,BLUE,0,12);
        build.setGravity(Gravity.CENTER);build.setMinHeight(dp(44));
        build.setBackground(ShellTheme.surface(this,ShellTheme.LILAC_SOFT,16,Color.TRANSPARENT));
        build.setContentDescription("版本号 "+BuildConfig.VERSION_NAME);
        build.setOnClickListener(view->tapAboutVersion());ShellDialogs.pressFeedback(build);
        LinearLayout versions = new LinearLayout(this);
        versions.setOrientation(LinearLayout.HORIZONTAL);
        JSONObject status = RuntimeStatus.read(this);
        for (String name : new String[]{"kira", "napcat"}) {
            JSONObject component = RuntimeStatus.component(status, name);
            String installed = component.isNull("version") ? "" : component.optString("version");
            addVersionTile(versions, "kira".equals(name) ? "KiraAI" : "NapCat",
                installed.isEmpty() ? "未读取到组件版本" : installed,
                "kira".equals(name) ? BLUE : ShellTheme.TEAL);
        }
        content.addView(versions, new LinearLayout.LayoutParams(-1, -2));
        TextView note = dialogCopy(content, "账号登录、模型与业务配置由各自官方面板管理。", 11, MUTED, 15, 3);
        note.setGravity(Gravity.CENTER);
        dialogCopy(content,"项目主页",12,BLUE,15,4).setAccessibilityHeading(true);
        addProjectLink(content,"01","KiraAI 手机版","Android 环境与组件管理","https://github.com/fcll32/KiraAI-Android");
        addProjectLink(content,"02","KiraAI","AI 核心与官方管理面板","https://github.com/KiraAI-Dev/KiraAI");
        addProjectLink(content,"03","NapCatQQ（fork）","QQ 消息桥接组件 · 原仓库已删库","https://github.com/xxynet/NapCatQQ/releases/tag/v4.9.43");
        ShellDialogs.builder(this,ShellDialogs.Card.ABOUT).setTitle("关于").setView(ShellDialogs.scroll(this,content,.56f))
            .setPositiveButton("关闭",null).show();
    }

    /** 版本号连续五次轻点，仅打开换 UI 彩蛋入口，不改变当前主题或运行状态。 */
    private void tapAboutVersion() {
        if(easterEggOpen) return;
        long now=android.os.SystemClock.elapsedRealtime();
        if(now-lastAboutVersionTap>1200) aboutVersionTaps=0;
        lastAboutVersionTap=now;
        if(++aboutVersionTaps<5) return;
        aboutVersionTaps=0;easterEggOpen=true;
        LinearLayout content=ShellCards.column(this,20);
        LinearLayout emblem=new LinearLayout(this);emblem.setGravity(Gravity.CENTER);
        emblem.addView(ShellIcons.view(this,ShellIcons.SETTINGS),new LinearLayout.LayoutParams(dp(64),dp(64)));
        content.addView(emblem,new LinearLayout.LayoutParams(-1,dp(76)));
        ShellCards.section(content,"隐藏入口","另一片天空","换 UI 的彩蛋入口已找到。更多界面风格正在准备中。",BLUE);
        AlertDialog egg=ShellDialogs.builder(this,ShellDialogs.Card.ABOUT).setTitle("界面彩蛋").setView(content)
            .setPositiveButton("期待一下",null).create();
        egg.setOnDismissListener(ignored->{easterEggOpen=false;aboutVersionTaps=0;lastAboutVersionTap=0;});egg.show();
    }

    private void addProjectLink(LinearLayout parent,String number,String name,String summary,String url) {
        LinearLayout row=new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(12),dp(10),dp(12),dp(10));
        row.setBackground(ShellTheme.surface(this,ShellTheme.SOFT,16,Color.TRANSPARENT));
        TextView mark=ShellCards.text(this,number,11,BLUE); row.addView(mark,new LinearLayout.LayoutParams(dp(30),-2));
        LinearLayout copy=new LinearLayout(this); copy.setOrientation(LinearLayout.VERTICAL);
        TextView heading=ShellCards.text(this,name,14,INK); heading.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL)); copy.addView(heading);
        copy.addView(ShellCards.text(this,summary,11,MUTED)); row.addView(copy,new LinearLayout.LayoutParams(0,-2,1));
        row.addView(ShellCards.text(this,"↗",20,BLUE));
        row.setContentDescription("打开 "+name+" 项目主页"); row.setFocusable(true);
        row.setOnClickListener(view -> {
            try { startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url))); }
            catch (RuntimeException failure) { toast("无法打开项目主页"); }
        });
        ShellDialogs.pressFeedback(row);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2); params.topMargin=dp(8); parent.addView(row,params);
    }

    private void showDeviceStatus() {
        JSONObject value=RuntimeStatus.read(this); boolean live=RuntimeStatus.alive(this);
        LinearLayout content=ShellCards.column(this,20);
        ShellCards.section(content,"本机状态",RuntimeStatus.shellState(value,live),RuntimeStatus.title(value),BLUE);
        ShellCards.pair(content,"手机",Build.MANUFACTURER+" "+Build.MODEL,BLUE);
        ShellCards.pair(content,"系统","Android "+Build.VERSION.RELEASE+" · API "+Build.VERSION.SDK_INT,BLUE);
        ShellCards.pair(content,"环境",value.optString("detail","尚未启动"),BLUE);
        ShellCards.pair(content,"可用存储",RuntimeStatus.bytes(getFilesDir().getUsableSpace()),BLUE);
        JSONObject space=RuntimeStatus.readJson(new File(RuntimeStatus.directory(this), "space-preflight.json"));
        JSONArray volumes=space.optJSONArray("volumes");
        if (volumes!=null && volumes.length()>0) {
            StringBuilder estimate=new StringBuilder();
            for (int i=0;i<volumes.length();i++) {
                JSONObject volume=volumes.optJSONObject(i);
                if (volume==null) continue;
                if (estimate.length()>0) estimate.append('\n');
                estimate.append("预计需 ").append(RuntimeStatus.bytes(volume.optLong("required_bytes"))).append(" 空闲");
                if (!volume.isNull("available_bytes"))
                    estimate.append(" · 检查时可用 ").append(RuntimeStatus.bytes(volume.optLong("available_bytes")));
            }
            if (estimate.length()>0) ShellCards.pair(content,"最近空间预检",estimate.toString(),BLUE);
        }
        // R5C3: what the last start-up clean-up removed, and what it left for the user to look at.
        JSONObject retention=RuntimeStatus.readJson(new File(RuntimeStatus.directory(this), "retention-report.json"));
        if (retention.has("version")) {
            JSONArray review=retention.optJSONArray("kept_for_review");
            String summary="上次启动回收 "+RuntimeStatus.bytes(retention.optLong("reclaimed_total_bytes"));
            if (retention.optBoolean("package_caches_cleaned")) summary+="\n空间不足，已清理 APT/pip/npm 缓存";
            if (review!=null && review.length()>0) {
                summary+="\n以下 "+review.length()+" 项未自动删除，请自行确认：";
                for (int i=0;i<Math.min(3,review.length());i++) {
                    JSONObject item=review.optJSONObject(i);
                    if (item!=null) summary+="\n· "+item.optString("kind")+" / "+item.optString("name")+"（"+item.optString("reason")+"）";
                }
                if (review.length()>3) summary+="\n· 另有 "+(review.length()-3)+" 项";
            }
            if (retention.optJSONArray("errors")!=null && retention.optJSONArray("errors").length()>0) summary+="\n部分项目清理失败，下次启动重试";
            ShellCards.pair(content,"自动清理",summary,BLUE);
        }
        for (String id:new String[]{"kira","napcat"}) {
            JSONObject component=RuntimeStatus.component(value,id);
            String description=RuntimeStatus.phase(component.optString("state","stopped"));
            if ("napcat".equals(id)) description += napcatInstalled(value) ? " · 已安装" : " · 未安装";
            description += selectedComponents.contains(id) ? " · 已选择" : " · 未选择";
            boolean ready=live && "ready".equals(component.optString("state"));
            if (ready) description+=" · "+RuntimeStatus.panelHealthLabel(RuntimeStatus.panelHealth(this, component));
            String version=component.optString("version","");
            if (!version.isEmpty() && !component.isNull("version")) description+=" · "+version;
            String detail=component.optString("detail","");
            if (!detail.isEmpty() && !component.isNull("detail") && !(ready && "官方面板已就绪".equals(detail))) description+="\n"+detail;
            String error=component.optString("error","");
            if (!error.isEmpty() && !component.isNull("error") && !error.equals(detail)) description+="\n"+error;
            ShellCards.pair(content,"kira".equals(id) ? "KiraAI" : "NapCat",description,BLUE);
        }
        if (RuntimeStatus.requiresCleanup(this, value)) {
            dialogCopy(content,"先停止全部组件，再检查残留进程。核验通过后可手动启动。",12,MUTED,12,8);
            addInlineAction(content,"重试清理",() -> confirmCleanup(true));
            addInlineAction(content,"核验后解除",() -> confirmCleanup(false));
        }
        ShellDialogs.builder(this,ShellDialogs.Card.DEVICE).setTitle("设备状态").setView(ShellDialogs.scroll(this,content,.50f))
            .setPositiveButton("关闭",null).show();
    }

    private void confirmCleanup(boolean retry) {
        ShellDialogs.builder(this,ShellDialogs.Card.DEVICE).setTitle(retry ? "重试清理" : "核验停止状态")
            .setMessage(retry ? "将停止全部组件，并尝试清理能够确认归属的残留进程。核验通过后解除启动阻断。"
                : "将停止全部组件并检查所属进程。确认全部退出后解除启动阻断，之后可手动启动。")
            .setPositiveButton(retry ? "重试清理" : "核验并解除",(dialog,which) ->
                sendComponentCommand("all",retry ? "retry_cleanup" : "verify_cleanup"))
            .setNegativeButton("取消",null).show();
    }

    private String initialToken(String component) {
        if(!"kira".equals(component)&&!"napcat".equals(component))return "";
        File file=new File(RuntimeStatus.directory(this),"initial-"+component+"-token.json");
        JSONObject record=RuntimeStatus.readJson(file);
        if(!"recorded".equals(record.optString("state"))||record.isNull("token"))return "";
        String token=record.optString("token","");return token.length()>4096?"":token;
    }

    private void showInitialToken(String component) {
        String name="kira".equals(component)?"KiraAI":"NapCat";
        String token=initialToken(component);
        LinearLayout content=ShellCards.column(this,20);
        ShellCards.section(content,"首次生成的记录",name+" 默认令牌",
            "仅保留首次配置时的默认值；后续修改面板令牌，不会更新这里的记录。",BLUE);
        if(token.isEmpty()) {
            dialogCopy(content,"尚未记录首次默认令牌。新安装需先完成组件准备；旧版未留下记录时，无法可靠恢复最初的值。",13,MUTED,0,10);
            dialogCopy(content,"这里不会读取您后来修改的令牌，也不会重新生成或重置面板密码。",12,BLUE,0,6);
        } else {
            TextView value=dialogCopy(content,"••••••••••••••••",14,INK,0,8);
            value.setTypeface(Typeface.MONOSPACE);value.setMinLines(2);
            value.setPadding(dp(12),dp(10),dp(12),dp(10));
            value.setBackground(ShellTheme.surface(this,ShellTheme.SOFT,16,Color.TRANSPARENT));
            value.setContentDescription("首次默认令牌已隐藏");
            Button reveal=addInlineAction(content,"显示首次默认令牌",()->{});
            final boolean[] visible={false};
            reveal.setOnClickListener(view->{
                visible[0]=!visible[0];value.setText(visible[0]?token:"••••••••••••••••");
                value.setTextIsSelectable(visible[0]);value.setContentDescription(visible[0]?null:"首次默认令牌已隐藏");
                reveal.setText(visible[0]?"隐藏令牌":"显示首次默认令牌");
            });
        }
        dialogCopy(content,"如何使用",13,BLUE,14,5).setAccessibilityHeading(true);
        dialogCopy(content,"1. 在底栏打开 "+name+" 面板。\n2. 如果仍使用首次默认值，复制后粘贴到该面板的令牌登录框。\n3. KiraAI 与 NapCat 的令牌分别使用，不能混用。",12,MUTED,0,10);
        TextView note=dialogCopy(content,"如果您后来改过令牌，此处显示的默认值可能已无法登录。请使用您自己设置的新值，应用不会显示它。",12,BLUE,0,0);
        note.setPadding(dp(12),dp(10),dp(12),dp(10));note.setBackground(ShellTheme.surface(this,ShellTheme.ROSE_SOFT,16,Color.TRANSPARENT));
        AlertDialog dialog=ShellDialogs.builder(this,"kira".equals(component) ? ShellDialogs.Card.KIRA_TOKEN : ShellDialogs.Card.NAPCAT_TOKEN)
            .setTitle(name+" 首次默认令牌")
            .setView(ShellDialogs.scroll(this,content,.52f)).setPositiveButton("复制默认令牌",null).setNegativeButton("关闭",null).create();
        dialog.setOnShowListener(ignored->{Button copy=dialog.getButton(AlertDialog.BUTTON_POSITIVE);copy.setEnabled(!token.isEmpty());copy.setOnClickListener(view->copyToken(component));});
        dialog.show();
    }

    private void copyToken(String component) {
        String token=initialToken(component);
        if(token.isEmpty()){toast("没有可复制的首次默认令牌");return;}
        ClipboardManager clipboard=getSystemService(ClipboardManager.class);
        ClipData clip=ClipData.newPlainText(("kira".equals(component)?"KiraAI":"NapCat")+" 首次默认令牌",token);
        android.os.PersistableBundle extras=new android.os.PersistableBundle();
        extras.putBoolean("android.content.extra.IS_SENSITIVE",true);clip.getDescription().setExtras(extras);
        clipboard.setPrimaryClip(clip);
        toast("已复制首次默认令牌；修改过后请使用您自己设置的新值");
    }

    private void showLog(String component) {
        if (!"environment".equals(component) && !"napcat".equals(component)) return;
        boolean terminal = "napcat".equals(component);
        LinearLayout content = ShellCards.column(this, terminal ? 10 : 18);
        dialogCopy(content, "仅显示最近一段记录，刷新不会改变日志文件。", 11, terminal ? 0xFFD5C3DC : MUTED, 0, 11);
        LinearLayout reading = ShellCards.column(this, 12);
        reading.setPadding(dp(13), dp(11), dp(13), dp(13));
        // 日志阅读区是一张圆角卡：浅色带细边，终端色不描边；内容按圆角轮廓裁剪。
        reading.setBackground(ShellTheme.surface(this, terminal ? ShellTheme.NIGHT : ShellTheme.SURFACE, 18,
            terminal ? Color.TRANSPARENT : ShellTheme.LINE));
        reading.setClipToOutline(true);
        addLogToolbar(reading, terminal);
        View rule = new View(this);
        rule.setBackgroundColor(terminal ? 0xFF6A506F : ShellTheme.LINE);
        LinearLayout.LayoutParams ruleParams = new LinearLayout.LayoutParams(-1, dp(1));
        ruleParams.topMargin = dp(10);
        ruleParams.bottomMargin = dp(2);
        reading.addView(rule, ruleParams);
        ShellLogView log = new ShellLogView(this);
        reading.addView(log, new LinearLayout.LayoutParams(-1, -2));
        content.addView(reading, new LinearLayout.LayoutParams(-1, -2));
        ScrollView scroll = ShellDialogs.scroll(this, content, terminal ? .66f : .38f);
        scroll.setMinimumHeight(dp(terminal ? 180 : 120));
        loadLog(component, log);
        AlertDialog dialog = ShellDialogs.builder(this, terminal ? ShellDialogs.Card.NAPCAT_LOG : ShellDialogs.Card.ENVIRONMENT_LOG)
            .setTitle("napcat".equals(component) ? "NapCat 日志" : "环境安装日志")
            .setView(scroll).setPositiveButton("关闭", null).setNeutralButton("刷新", null).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_NEUTRAL)
            .setOnClickListener(view -> loadLog(component, log)));
        dialog.show();
    }

    private void loadLog(String component, ShellLogView log) {
        File file = RuntimeStatus.componentLog(this, RuntimeStatus.read(this), component);
        try (RandomAccessFile input = new RandomAccessFile(file, "r")) {
            int count = (int) Math.min(input.length(), 32768);
            byte[] bytes = new byte[count];
            input.seek(input.length() - count);
            input.readFully(bytes);
            log.setLogText(new String(bytes, StandardCharsets.UTF_8));
        } catch (Exception error) { log.setLogText("该日志暂未生成。"); }
        if ("napcat".equals(component)) tintLogText(log, 0xFFE5D5EB);
    }

    private void batterySettings() {
        PowerManager power = getSystemService(PowerManager.class);
        boolean exempt = power.isIgnoringBatteryOptimizations(getPackageName());
        LinearLayout content = ShellCards.column(this, 20);
        LinearLayout state = new LinearLayout(this);
        state.setGravity(Gravity.CENTER_VERTICAL);
        state.setPadding(dp(15), dp(15), dp(15), dp(15));
        state.setBackground(ShellTheme.surface(this,ShellTheme.tint(ShellTheme.TEAL),18,ShellTheme.LINE));
        FrameLayout dial = new FrameLayout(this);
        TextView marker = ShellCards.text(this, exempt ? "✓" : "!", 19, ShellTheme.TEAL);
        marker.setGravity(Gravity.CENTER);
        marker.setBackgroundColor(Color.TRANSPARENT);
        dial.addView(marker, new FrameLayout.LayoutParams(dp(40), dp(40), Gravity.CENTER));
        TextView dialCaption = ShellCards.text(this, exempt ? "已允许" : "待调整", 8, ShellTheme.TEAL);
        dialCaption.setGravity(Gravity.CENTER);
        dialCaption.setTypeface(Typeface.MONOSPACE);
        FrameLayout.LayoutParams dialCaptionParams = new FrameLayout.LayoutParams(dp(62), dp(16), Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        dialCaptionParams.bottomMargin = dp(-8);
        dial.addView(dialCaption, dialCaptionParams);
        state.addView(dial, new LinearLayout.LayoutParams(dp(64), dp(52)));
        LinearLayout statusCopy = ShellCards.column(this, 0);
        statusCopy.setPadding(dp(12), 0, 0, 0);
        TextView current = dialogCopy(statusCopy, exempt ? "后台运行已放行" : "允许持续后台运行", 19, ShellTheme.TEAL, 0, 3);
        current.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        dialogCopy(statusCopy, exempt ? "本应用不受系统电池优化限制" : "先检查系统电池优化限制", 11, MUTED, 0, 0);
        state.addView(statusCopy, new LinearLayout.LayoutParams(0, -2, 1));
        content.addView(state, new LinearLayout.LayoutParams(-1, -2));
        dialogCopy(content,"后台运行检查",12,ShellTheme.TEAL,16,0).setAccessibilityHeading(true);
        addBatteryTip(content, "保留通知", "运行时请保留常驻通知，便于系统维持服务状态。");
        addBatteryTip(content, "允许后台活动", "在手机的应用管理中允许后台活动；厂商的省电策略仍可能结束运行。");
        ShellDialogs.builder(this, ShellDialogs.Card.BATTERY).setTitle("后台与电池设置").setView(ShellDialogs.scroll(this, content, .44f))
            .setPositiveButton("打开系统设置", (dialog, which) -> {
                try { startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)); }
                catch (RuntimeException error) { startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName()))); }
            }).setNegativeButton("关闭", null).show();
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (updater.result(request, result, data)) return;
        if (backups.result(request, result, data)) return;
        for (PanelDownloads downloads : panelDownloads) if (downloads.result(request, result, data)) return;
        if (request != CHOOSE_UPLOAD || upload == null) return;
        ArrayList<Uri> selected = new ArrayList<>();
        if (result == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                for (int index = 0; index < Math.min(32, data.getClipData().getItemCount()); index++) {
                    Uri uri = data.getClipData().getItemAt(index).getUri();
                    if (uri != null && "content".equals(uri.getScheme())) selected.add(uri);
                }
            } else if (data.getData() != null && "content".equals(data.getData().getScheme())) selected.add(data.getData());
        }
        upload.onReceiveValue(selected.isEmpty() ? null : selected.toArray(new Uri[0]));
        upload = null;
    }

    @Override protected void onResume() {
        super.onResume();
        if(selectedPanel!=null&&!showingEnvironment){int index="napcat".equals(selectedPanel)?1:0;if(panelLoading[index])workspace.beginNavigation(index+1,true);}
        handler.post(refresh);for(WebView panel:panelViews)panel.onResume();
    }
    @Override protected void onPause() {
        handler.removeCallbacks(refresh);
        clearNotice();
        navigationGeneration++;navigationQueued=false;workspace.cancelLoading();
        workspace.finishTransition();
        for (FrameLayout card:componentCards) {
            card.animate().cancel(); card.setRotationY(0); card.setTag(Boolean.FALSE);
        }
        for (WebView panel : panelViews) panel.onPause(); CookieManager.getInstance().flush(); super.onPause();
    }
    @Override public void onBackPressed() {
        if (onboarding.isShowing()) onboarding.close();
        else if (drawer.isOpen()) drawer.close();
        else if (!showingEnvironment && web.canGoBack()) web.goBack();
        else if (!showingEnvironment) showEnvironment();
        else super.onBackPressed();
    }
    @Override protected void onDestroy() {
        handler.removeCallbacks(refresh);
        if (nightAnimator != null) { nightAnimator.removeAllUpdateListeners(); nightAnimator.cancel(); nightAnimator = null; }
        ShellDialogs.dispose(this);
        clearNotice();
        if (upload != null) { upload.onReceiveValue(null); upload = null; }
        updater.destroy();
        backups.destroy();
        onboarding.dispose();
        for (PanelDownloads downloads : panelDownloads) downloads.destroy();
        navigation.dispose();
        drawer.dispose();
        workspace.dispose();
        for (WebView panel : panelViews) panel.destroy();
        super.onDestroy();
    }

    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + 0.5f); }
    private void toast(String message) {
        long now = android.os.SystemClock.elapsedRealtime();
        if (message.equals(noticeText) && now < nextNoticeAt) return;
        clearNotice();
        noticeText = message;
        nextNoticeAt = now + 2000;
        notice = Toast.makeText(this, message, Toast.LENGTH_SHORT);
        notice.show();
    }
    private void clearNotice() {
        if (notice != null) notice.cancel();
        notice = null; noticeText = null; nextNoticeAt = 0;
    }
}
